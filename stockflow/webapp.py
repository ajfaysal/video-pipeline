"""
webapp.py - Mini-App backend (Telegram Web App + browser).

Stdlib-threaded HTTP server serving:
    GET  /                     -> glassmorphism Mini-App (miniapp/index.html)
    GET  /api/status           -> config summary (keys masked)
    GET  /api/niches           -> market-intelligence suggestions
    POST /api/generate         -> run one production job, return bundle meta
    GET  /api/jobs             -> recent jobs
    GET  /api/file/<job>/<fn>  -> one-click HD download of asset/metadata
    POST /api/settings/engine  -> switch default engine
    POST /api/settings/add     -> add a custom engine
    POST /api/settings/key     -> set an engine API key
    POST /api/settings/defaults-> update defaults (resolution/format/upscale)

Run standalone:
    python -m stockflow.webapp --port 8080
or it is auto-started by bot.py alongside the Telegram polling loop.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .config import ENGINE_LABELS, Config, load_config
from .engines import EngineError
from . import niches as niche_engine
from . import pipeline

_MINIAPP_HTML = os.path.join(os.path.dirname(__file__), "miniapp", "index.html")

_config: Optional[Config] = None
_config_lock = threading.Lock()


def get_config() -> Config:
    global _config
    with _config_lock:
        if _config is None:
            _config = load_config()
    return _config


def _jsonable_engines(cfg: Config) -> Dict[str, Any]:
    engines = {}
    for eid, eng in cfg.engines.items():
        engines[eid] = {
            "label": eng.get("label", eid),
            "api_format": eng.get("api_format"),
            "base_url": eng.get("base_url"),
            "model": eng.get("model"),
            "has_key": bool(cfg.api_key_for({"api_key": eng.get("api_key"),
                                             "api_key_env": eng.get("api_key_env")})),
            "masked_key": cfg.mask_key(eng),
            "builtin": eid in ENGINE_LABELS,
        }
    return engines


class MiniAppHandler(BaseHTTPRequestHandler):
    server_version = "StockFlowAI/1.0"

    # ---------- helpers ----------

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str, download_name: Optional[str] = None) -> None:
        with open(path, "rb") as fh:
            body = fh.read()
        ext = os.path.splitext(path)[1].lower()
        ctype = {
            ".png": "image/png", ".svg": "image/svg+xml",
            ".json": "application/json", ".html": "text/html; charset=utf-8",
        }.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if download_name:
            self.send_header("Content-Disposition",
                             f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 1_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter logs
        print(f"[miniapp] {self.address_string()} - {fmt % args}")

    # ---------- routes ----------

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/":
            if os.path.isfile(_MINIAPP_HTML):
                return self._send_file(_MINIAPP_HTML)
            return self._send_json({"error": "miniapp html missing"}, 500)
        if path == "/api/status":
            cfg = get_config()
            return self._send_json({
                "ok": True,
                "defaults": cfg.defaults,
                "engines": _jsonable_engines(cfg),
                "output_dir": pipeline.output_dir(),
                "registry_size": len(pipeline.get_registry()),
            })
        if path == "/api/niches":
            return self._send_json({"ok": True,
                                    "niches": niche_engine.niches_as_dicts(top_n=6)})
        if path == "/api/jobs":
            return self._send_json({"ok": True, "jobs": pipeline.latest_jobs(limit=12)})
        if path.startswith("/api/file/"):
            parts = path.split("/")
            if len(parts) == 5:
                job_id, filename = parts[3], parts[4]
                resolved = pipeline.job_file(job_id, filename)
                if resolved:
                    return self._send_file(resolved, download_name=filename)
            return self._send_json({"error": "not found"}, 404)
        return self._send_json({"error": "unknown route"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        body = self._read_body()
        cfg = get_config()

        if path == "/api/generate":
            prompt = (body.get("prompt") or "").strip()
            if not prompt:
                return self._send_json({"error": "prompt required"}, 400)
            try:
                bundle = pipeline.run_production(
                    cfg, prompt,
                    niche=body.get("niche") or None,
                    engine_id=body.get("engine") or None,
                    resolution=body.get("resolution") or None,
                    aspect=body.get("aspect") or None,
                    out_format=body.get("format") or None,
                    upscale=body.get("upscale"),
                )
                meta = bundle.metadata_dict()
                meta["ok"] = True
                meta["download"] = f"/api/file/{bundle.job_id}/asset.{meta['files'][0].split('.')[-1]}"
                return self._send_json(meta)
            except EngineError as exc:
                return self._send_json({"error": str(exc)}, 502)
            except Exception as exc:  # never leak stack to client
                return self._send_json({"error": f"generation failed: {exc}"}, 500)

        if path == "/api/settings/engine":
            engine_id = (body.get("engine") or "").strip()
            if engine_id not in cfg.engines:
                return self._send_json({"error": "unknown engine"}, 400)
            cfg.set_default("engine", engine_id)
            return self._send_json({"ok": True, "engine": engine_id})

        if path == "/api/settings/defaults":
            allowed = {"resolution", "format", "upscale", "aspect",
                       "title_length", "tag_count"}
            updates = {k: v for k, v in body.items() if k in allowed}
            for k, v in updates.items():
                cfg.defaults[k] = v
            cfg.save()
            return self._send_json({"ok": True, "defaults": cfg.defaults})

        if path == "/api/settings/key":
            engine_id = (body.get("engine") or "").strip()
            key = (body.get("api_key") or "").strip()
            if engine_id not in cfg.engines or not key:
                return self._send_json({"error": "engine + api_key required"}, 400)
            cfg.engines[engine_id]["api_key"] = key
            cfg.save()
            return self._send_json({"ok": True})

        if path == "/api/settings/add":
            try:
                cfg.add_engine(
                    engine_id=body.get("id", ""),
                    api_format=body.get("api_format", "openai-compatible"),
                    base_url=body.get("base_url", ""),
                    model=body.get("model", ""),
                    api_key=body.get("api_key", ""),
                    label=body.get("label", ""),
                )
            except ValueError as exc:
                return self._send_json({"error": str(exc)}, 400)
            return self._send_json({"ok": True, "engines": list(cfg.engines)})

        return self._send_json({"error": "unknown route"}, 404)


def run_server(port: int = 8080, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), MiniAppHandler)
    print(f"[stockflow] Mini-App listening on http://{host}:{port}")
    server.serve_forever()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="StockFlow AI Mini-App server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    run_server(port=args.port, host=args.host)


if __name__ == "__main__":
    main()
