# Skill: New Tool Scaffold

> **Trigger**: "new tool", "scaffold a tool", "new CLI", "add a new video tool"

## Reference pattern (study these two before creating a new tool)

- **Simple, single-file tool**: `abroll/main.py` — one file, imports shared
  helpers from `aspectshift/downloader.py`, no tool-specific submodules.
- **Multi-module tool**: `watermarkwipe/` (`main.py` + `watermark_detector.py`
  + `watermark_remover.py`) or `aspectshift/` (`main.py` + `downloader.py` +
  `converter.py` + `enhance.py` + `thumbnail.py`) — split when the tool has
  genuinely separable concerns (detection vs. removal, download vs. encode).

## Procedure

### 1. Plan
- Pick a **PascalCase product name** matching the existing style (LofiLoop,
  AspectShift, ClipHarvest, WatermarkWipe, ABRoll, IntroOutro, Stitcher,
  AudioDuck, LoudNorm, AutoChapters) and a **lowercase folder name** with no
  separator (`newtool/`, not `new-tool/` or `new_tool/`).
- Decide: single-file or multi-module? Default to single-file until a second
  concern (e.g. a distinct detection stage) justifies a split.

### 2. Scaffold the folder
```
newtool/
├── __init__.py       # empty, matches every other tool folder
└── main.py           # CLI entry point
```

### 3. Write `main.py` following the shared skeleton
```python
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Reuse existing shared helpers instead of duplicating download/probe logic —
# aspectshift.downloader is the de facto shared utility module used by
# abroll, autochapters, loudnorm, etc.
from aspectshift.downloader import resolve_input, probe_video


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="NewTool - <one-line purpose>")
    p.add_argument("--input", help="Local video file path")
    p.add_argument("--url", help="Video URL (yt-dlp compatible or direct)")
    p.add_argument("--output-dir", default="./output", help="Output directory")
    # ... tool-specific flags here, following existing naming (kebab-case
    # flags, e.g. --target-lufs not --targetLufs) ...
    return p


def main() -> int:
    args = build_parser().parse_args()
    os.makedirs(args.output_dir, exist_ok=True)
    try:
        source = resolve_input(url=args.url, output_dir=args.output_dir) if args.url \
            else resolve_input(input_path=args.input, output_dir=args.output_dir)
        # ... core logic, ffmpeg via subprocess.run() ...
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

### 4. Wire into `bot/run_job.py`
- Add `run_newtool(chat_id: str) -> None:` following the exact shape of the
  other `run_*` functions (read env vars via `_env()`, build `cmd` list,
  `_run(cmd)`, then send results via `_send_output_videos`/`_send_output_documents`).
- Add `"newtool": run_newtool,` to the `TOOL_RUNNERS` dict at the bottom.
- Document every new env var in the module docstring at the top of the file.

### 5. Wire into the Telegram bot (if it should be user-facing)
- Add to `TOOL_LABELS` and one `TOOL_GROUPS` entry in `cloudflare-worker/worker.js`.
- Add a case to `sourceInstructions(tool)`.
- Add any needed option-collection steps to `continueAfterSource()`.
- Add the new options to the `options: {...}` object inside `dispatchAndFinish()`.
- See `.agent/skills/add-telegram-command.md` for the full safe-editing procedure.

### 6. Add the GitHub Actions env mapping
- Add the new tool's env vars to `.github/workflows/telegram-dispatch.yml`'s
  `env:` block (or, if blocked by workflow-write permissions, to
  `docs/WORKFLOW_CHANGES_REQUIRED.md`-style manual instructions).
- Optionally add a standalone `workflow_dispatch` workflow
  `.github/workflows/newtool.yml` mirroring `abroll.yml`/`loudnorm.yml` for
  manual runs from the Actions tab.

### 7. Document
- Add a numbered section to `README.md` matching the existing tools' format
  (code block usage examples, bullet list of behavior, "Known limitation" if
  applicable).
- Add the tool to the table in `AGENTS.md`'s "Tools" section and to the
  `Repository Structure` tree.

### 8. Verify
```bash
python newtool/main.py --help          # loads without import errors
python -c "import bot.run_job"         # TOOL_RUNNERS wiring didn't break imports
node -c cloudflare-worker/worker.js    # if worker.js was touched
```

### 9. Commit
`feat(newtool): scaffold <ToolName> — <one-line purpose>`

## Example

**Input**: "Add a new tool called SubtitleBurn that burns an SRT file onto a video"

**Output**:
```
Files created:
  subtitleburn/__init__.py
  subtitleburn/main.py           — --input/--url, --srt, --output-dir, ffmpeg subtitles filter

Files changed:
  bot/run_job.py                 — added run_subtitleburn(), added to TOOL_RUNNERS
  cloudflare-worker/worker.js     — added to TOOL_LABELS/TOOL_GROUPS, sourceInstructions,
                                    a srt-collection step in continueAfterSource()
  README.md                      — new "11. SubtitleBurn" section

Commit: feat(subtitleburn): scaffold SubtitleBurn tool for SRT burn-in
```
