"""
Doctor CLI — quick health check for the video content pipeline.

Verifies system binaries, Python packages, and tool entry points so you can
spot a broken environment before kicking off a long ffmpeg job.

Usage:
    python doctor/main.py
    python doctor/main.py --json
    python doctor/main.py --strict
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Tool folders that ship a main.py CLI entry point
TOOLS: list[tuple[str, str]] = [
    ("LofiLoop", "lofiloop"),
    ("AspectShift", "aspectshift"),
    ("ClipHarvest", "clipharvest"),
    ("WatermarkWipe", "watermarkwipe"),
    ("ABRoll", "abroll"),
    ("IntroOutro", "introoutro"),
    ("Stitcher", "stitcher"),
    ("AudioDuck", "audioduck"),
    ("LoudNorm", "loudnorm"),
    ("AutoChapters", "autochapters"),
    ("PhotoStudio", "photostudio"),
]

# (binary name, human label)
BINARIES: list[tuple[str, str]] = [
    ("ffmpeg", "ffmpeg"),
    ("ffprobe", "ffprobe"),
]

# Optional Python packages used by subsets of tools
PACKAGES: list[tuple[str, str]] = [
    ("cv2", "opencv-python-headless"),
    ("numpy", "numpy"),
    ("yt_dlp", "yt-dlp"),
    ("requests", "requests"),
    ("librosa", "librosa"),
    ("faster_whisper", "faster-whisper"),
    ("gdown", "gdown"),
    ("pyrogram", "pyrogram"),
]


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    required: bool = True


@dataclass
class Report:
    checks: list[CheckResult] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.ok)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if not c.ok and c.required)

    @property
    def warned(self) -> int:
        return sum(1 for c in self.checks if not c.ok and not c.required)

    @property
    def healthy(self) -> bool:
        return self.failed == 0


def _run_version(cmd: list[str]) -> str | None:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (result.stdout or result.stderr or "").strip()
    if not text:
        return None
    return text.splitlines()[0][:120]


def check_python() -> CheckResult:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    ok = sys.version_info >= (3, 11)
    detail = f"Python {version}" + ("" if ok else " (need 3.11+)")
    return CheckResult(name="python", ok=ok, detail=detail, required=True)


def check_binary(binary: str, label: str) -> CheckResult:
    path = shutil.which(binary)
    if not path:
        return CheckResult(
            name=label,
            ok=False,
            detail="not found on PATH",
            required=True,
        )
    version = _run_version([binary, "-version"]) or _run_version([binary, "--version"])
    detail = f"{path}" + (f" — {version}" if version else "")
    return CheckResult(name=label, ok=True, detail=detail, required=True)


def check_package(import_name: str, pip_name: str) -> CheckResult:
    try:
        mod = importlib.import_module(import_name)
    except ImportError:
        return CheckResult(
            name=pip_name,
            ok=False,
            detail="not installed (optional for some tools)",
            required=False,
        )
    version = getattr(mod, "__version__", None) or getattr(mod, "VERSION", None)
    if isinstance(version, tuple):
        version = ".".join(str(v) for v in version[:3])
    detail = f"importable as {import_name}"
    if version:
        detail += f" ({version})"
    return CheckResult(name=pip_name, ok=True, detail=detail, required=False)


def check_tools() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for display, folder in TOOLS:
        main_py = os.path.join(_REPO_ROOT, folder, "main.py")
        exists = os.path.isfile(main_py)
        results.append(
            {
                "name": display,
                "folder": folder,
                "entry": f"{folder}/main.py",
                "ok": exists,
            }
        )
    return results


def run_checks() -> Report:
    report = Report()
    report.checks.append(check_python())
    for binary, label in BINARIES:
        report.checks.append(check_binary(binary, label))
    for import_name, pip_name in PACKAGES:
        report.checks.append(check_package(import_name, pip_name))
    report.tools = check_tools()
    return report


def _mark(ok: bool) -> str:
    return "OK" if ok else "!!"


def print_human(report: Report) -> None:
    print("Video Pipeline Doctor")
    print("=" * 40)
    print()
    print("Environment")
    print("-" * 40)
    for check in report.checks:
        status = _mark(check.ok)
        flag = "" if check.required or check.ok else " [optional]"
        print(f"  [{status}] {check.name}: {check.detail}{flag}")

    print()
    print("Tools")
    print("-" * 40)
    for tool in report.tools:
        status = _mark(tool["ok"])
        print(f"  [{status}] {tool['name']:14} → {tool['entry']}")

    print()
    print("Summary")
    print("-" * 40)
    print(f"  passed : {report.passed}")
    print(f"  failed : {report.failed}")
    print(f"  warned : {report.warned} (optional packages missing)")
    print(f"  tools  : {sum(1 for t in report.tools if t['ok'])}/{len(report.tools)}")
    print()
    if report.healthy:
        print("  Status : healthy — ready to process video")
    else:
        print("  Status : unhealthy — fix required checks above")


def print_json(report: Report) -> None:
    payload = {
        "healthy": report.healthy,
        "passed": report.passed,
        "failed": report.failed,
        "warned": report.warned,
        "checks": [asdict(c) for c in report.checks],
        "tools": report.tools,
    }
    print(json.dumps(payload, indent=2))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnose the video-pipeline environment (binaries, packages, tools).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a human report.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any optional package is missing (not just required checks).",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    report = run_checks()

    if args.json:
        print_json(report)
    else:
        print_human(report)

    if not report.healthy:
        return 1
    if args.strict and report.warned:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
