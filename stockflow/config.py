"""
config.py - configuration persistence for StockFlow AI.

Single-file JSON store (``config.json`` in the repo root by default). Holds:

* The engine registry (Hugging Face default + any custom OpenAI / Stability /
  Midjourney-compatible endpoints the user adds).
* Per-engine secrets (or a reference to an environment variable - the
  preferred option so tokens never live in the JSON file).
* User defaults (resolution, format, engine, upscale, title length, tag count).

The same file drives BOTH the Telegram bot (``bot.py``) and the Mini-App
backend, so changing an engine in one place is instantly live in the other.
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_CONFIG_PATH = os.path.join(REPO_ROOT, "config.json")

# Engines that are built in and cannot be deleted (their keys/defaults can
# still be edited).
BUILTIN_ENGINES = ("huggingface", "openai", "stability", "midjourney")

# Map engine id -> the api_format the generator should speak.
ENGINE_FORMATS: Dict[str, str] = {
    "huggingface": "huggingface",
    "openai": "openai",
    "stability": "stability",
    "midjourney": "openai-compatible",  # Midjourney proxies are OpenAI-shaped
}

ENGINE_LABELS: Dict[str, str] = {
    "huggingface": "Hugging Face (FLUX.1-schnell) - free",
    "openai": "OpenAI (gpt-image-1 / DALL-E 3)",
    "stability": "Stability AI (SD3.5 / Ultra)",
    "midjourney": "Midjourney proxy (OpenAI-compatible)",
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "telegram": {
        "bot_token_env": "TELEGRAM_BOT_TOKEN",
        "webhook_secret": "",
        "miniapp_url": "",
        "public_base_url": "",
    },
    "defaults": {
        "engine": "huggingface",
        "resolution": "2k",        # 1k | 2k | 4k | 8k | 16k
        "format": "png",           # png | svg
        "upscale": False,          # staged-Lanczos post upscale to resolution tier
        "title_length": 70,        # Adobe Stock max title chars
        "tag_count": 50,           # aim for the full 50-tag allowance
        "aspect": "square",        # square | landscape | portrait
    },
    "engines": {
        "huggingface": {
            "label": ENGINE_LABELS["huggingface"],
            "api_format": "huggingface",
            "base_url": "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
            "model": "black-forest-labs/FLUX.1-schnell",
            "api_key": "",
            "api_key_env": "HF_TOKEN",
            "extra_headers": {},
            "options": {"wait_for_model": True},
        },
        "openai": {
            "label": ENGINE_LABELS["openai"],
            "api_format": "openai",
            "base_url": "https://api.openai.com/v1/images/generations",
            "model": "gpt-image-1",
            "api_key": "",
            "api_key_env": "OPENAI_API_KEY",
            "extra_headers": {},
            "options": {"size": "1024x1024", "quality": "high"},
        },
        "stability": {
            "label": ENGINE_LABELS["stability"],
            "api_format": "stability",
            "base_url": "https://api.stability.ai/v2beta/stable-image/generate/sd3",
            "model": "sd3.5-large",
            "api_key": "",
            "api_key_env": "STABILITY_API_KEY",
            "extra_headers": {},
            "options": {"output_format": "png", "aspect_ratio": "1:1"},
        },
        "midjourney": {
            "label": ENGINE_LABELS["midjourney"],
            "api_format": "openai-compatible",
            "base_url": "",
            "model": "midjourney",
            "api_key": "",
            "api_key_env": "MIDJOURNEY_API_KEY",
            "extra_headers": {},
            "options": {"size": "1024x1024"},
        },
    },
}


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge ``override`` onto ``base`` (override wins on leaves)."""
    out = deepcopy(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = val
    return out


class Config:
    """Load / mutate / persist the StockFlow configuration."""

    def __init__(self, path: str = DEFAULT_CONFIG_PATH):
        self.path = path
        self.data: Dict[str, Any] = deepcopy(DEFAULT_CONFIG)
        self.load()

    # ---------------- persistence ----------------

    def load(self) -> Dict[str, Any]:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    disk = json.load(fh)
                if isinstance(disk, dict):
                    self.data = _merge(self.data, disk)
            except (json.JSONDecodeError, OSError) as exc:
                # Never crash the bot on a corrupt config; fall back to defaults.
                print(f"[stockflow] WARNING: could not read {self.path}: {exc}")
        return self.data

    def save(self) -> None:
        """Atomic write: temp file + rename so a crash never leaves a half file."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".config-", suffix=".tmp", dir=os.path.dirname(self.path) or "."
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ---------------- convenience accessors ----------------

    @property
    def defaults(self) -> Dict[str, Any]:
        return self.data.setdefault("defaults", deepcopy(DEFAULT_CONFIG["defaults"]))

    def get_default(self, key: str, fallback: Any = None) -> Any:
        return self.defaults.get(key, fallback)

    def set_default(self, key: str, value: Any) -> None:
        self.defaults[key] = value
        self.save()

    @property
    def engines(self) -> Dict[str, Dict[str, Any]]:
        return self.data.setdefault("engines", {})

    def engine_ids(self) -> List[str]:
        return list(self.engines.keys())

    def get_engine(self, engine_id: Optional[str] = None) -> Dict[str, Any]:
        engine_id = engine_id or self.get_default("engine", "huggingface")
        engines = self.engines
        if engine_id not in engines:
            raise KeyError(
                f"Unknown engine '{engine_id}'. Available: {', '.join(engines)}"
            )
        eng = deepcopy(engines[engine_id])
        eng["id"] = engine_id
        return eng

    def add_engine(
        self,
        engine_id: str,
        api_format: str,
        base_url: str,
        model: str,
        api_key: str = "",
        api_key_env: str = "",
        label: str = "",
        extra_headers: Optional[Dict[str, str]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        engine_id = engine_id.strip().lower().replace(" ", "-")
        if not engine_id:
            raise ValueError("engine id may not be empty")
        self.engines[engine_id] = {
            "label": label or engine_id,
            "api_format": api_format,
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
            "api_key_env": api_key_env,
            "extra_headers": extra_headers or {},
            "options": options or {},
        }
        self.save()

    def remove_engine(self, engine_id: str) -> bool:
        if engine_id in BUILTIN_ENGINES:
            raise ValueError(f"'{engine_id}' is a built-in engine and cannot be removed")
        if engine_id in self.engines:
            del self.engines[engine_id]
            if self.get_default("engine") == engine_id:
                self.defaults["engine"] = "huggingface"
            self.save()
            return True
        return False

    def api_key_for(self, engine: Dict[str, Any]) -> str:
        """Resolve an engine's API key: inline value first, then env var."""
        if engine.get("api_key"):
            return engine["api_key"]
        env_name = engine.get("api_key_env") or ""
        return os.environ.get(env_name, "")

    def mask_key(self, engine: Dict[str, Any]) -> str:
        key = self.api_key_for(engine)
        if not key:
            return "(not set)"
        if len(key) <= 8:
            return "****"
        return f"{key[:4]}...{key[-4:]}"

    # ---------------- telegram ----------------

    def telegram(self) -> Dict[str, Any]:
        return self.data.setdefault("telegram", deepcopy(DEFAULT_CONFIG["telegram"]))

    def bot_token(self) -> str:
        tg = self.telegram()
        env_name = tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
        return os.environ.get(env_name, tg.get("bot_token", ""))


def load_config(path: Optional[str] = None) -> Config:
    return Config(path or os.environ.get("STOCKFLOW_CONFIG", DEFAULT_CONFIG_PATH))
