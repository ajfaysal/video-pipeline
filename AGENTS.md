# AGENTS.md — Video Content Pipeline

> Instructions for any AI coding agent (Grok Build, Claude Code, Codex, etc.)
> operating on this repository. Read this file first in every session.

## Project Overview

A **10-tool video-processing CLI suite** with a **Telegram bot front-end** that
requires zero VPS — Cloudflare Workers handle the bot interface, GitHub Actions
run the heavy ffmpeg/whisper compute, and results are delivered straight back
into the Telegram chat (up to 2 GB via MTProto).

### Tools

| Tool | Purpose |
|------|---------|
| **LofiLoop** | Loop short clip + long audio into seamless monetization-safe lofi video (2h–24h) |
| **AspectShift** | 16:9 → 9:16 with blur-fill or smart crop |
| **ClipHarvest** | Auto-extract best short-clip segments from long video |
| **WatermarkWipe** | Remove watermarks via inpaint or crop + optional color grading |
| **ABRoll** | Auto-detect cuts and insert B-roll with crossfades |
| **IntroOutro** | Add branded intro/outro cards with ffmpeg drawtext |
| **Stitcher** | Join clips with crossfade/wipe/cut transitions |
| **AudioDuck** | Duck background music under a voiceover track |
| **LoudNorm** | Normalize audio loudness to broadcast-standard LUFS |
| **AutoChapters** | Generate YouTube chapter timestamps from transcripts |

## Tech Stack

- **Language**: Python 3.11+
- **Core dependency**: ffmpeg / ffprobe (all video work)
- **Transcription**: faster-whisper (ClipHarvest, AutoChapters)
- **CV**: opencv-python-headless (WatermarkWipe, AspectShift)
- **Audio analysis**: librosa (ClipHarvest scoring)
- **Download**: yt-dlp, gdown (Google Drive)
- **Telegram delivery**: requests (Bot API), pyrogram + tgcrypto (MTProto for files >50 MB)
- **Bot interface**: Cloudflare Worker (JavaScript) with KV state
- **CI/CD**: GitHub Actions (workflow_dispatch + repository_dispatch)

## Repository Structure

```
<root>/
├── AGENTS.md               ← you are here
├── README.md               ← user-facing documentation
├── requirements.txt        ← combined pip dependencies
├── __init__.py
├── aspectshift/            ← downloader, converter, thumbnail, enhance, main
├── clipharvest/            ← transcriber, scorer, clipper, captioner, config, main
├── watermarkwipe/          ← watermark_detector, watermark_remover, main
├── abroll/                 ← main
├── introoutro/             ← main
├── stitcher/               ← main
├── audioduck/              ← main
├── loudnorm/               ← main
├── autochapters/           ← main
├── lofiloop/               ← downloader, render, uploader, main
├── bot/                    ← telegram_notify, run_job, mtproto_transfer
├── cloudflare-worker/      ← worker.js, wrangler.toml
├── docs/                   ← supplementary docs, workflow drafts
├── .agent/                 ← agent infrastructure (skills, hooks, memory)
├── .mcp/                   ← MCP server config template
└── .github/workflows/      ← per-tool + telegram-dispatch + agent-task
```

## Naming Conventions

- **Folders**: lowercase, single-word or hyphenated (`cloudflare-worker/`)
- **Python files**: `snake_case.py`
- **Workflow files**: `toolname.yml` (matches folder name)
- **Branches**: `main` (production), `genspark_ai_developer` (agent work)
- **Commit messages**: `type: short description` — types: `feat`, `fix`, `refactor`, `ci`, `docs`, `chore`, `agent`

## Coding Standards

### Python
- Use `from __future__ import annotations` in all new files
- Type hints on function signatures
- CLI entry points use `argparse` with `--output-dir` as a standard flag
- ffmpeg invocations go through `subprocess.run()` / `subprocess.call()`
- Encoding defaults: `libx264 -crf 16 -preset slow` (quality) or `-crf 18 -preset veryfast` (speed, LofiLoop)
- Error handling: raise `RuntimeError` with a human-readable message; the bot runner catches and forwards to Telegram

### JavaScript (Cloudflare Worker)
- Single `worker.js` file, ES module format
- State stored in Workers KV (binding: `BOT_STATE`)
- Secrets via `wrangler secret put`, never in code

### Tests & Linting
- If a `tests/` directory or linting config exists, run them before committing
- Currently: no formal test suite — validate by running the tool's `main.py --help` to confirm no import errors

## Mandatory Rules

1. **Never delete files** without an explicit `--confirm-delete` flag or direct human instruction
2. **Never commit secrets** — tokens, API keys, hashes go in GitHub Secrets or `.env` (which is gitignored)
3. **Always run existing tests/lint** before committing, if they exist
4. **Always preserve backward compatibility** — existing Telegram bot flows and workflow_dispatch interfaces must keep working
5. **Commit messages** must follow the `type: short description` format
6. **One logical change per commit** — don't bundle unrelated changes

## Trigger Architecture

```
User (mobile)
  │
  ▼
Telegram Bot (send video/link/command)
  │
  ▼
Cloudflare Worker (worker.js)
  ├── Parses command, collects options via inline buttons
  ├── Stores conversation state in KV
  └── Fires repository_dispatch → GitHub Actions
        │
        ▼
GitHub Actions (telegram-dispatch.yml)
  ├── Checks out repo, installs Python + ffmpeg
  ├── Runs bot/run_job.py with env vars from client_payload
  ├── Tool processes video (ffmpeg, whisper, etc.)
  └── Sends result back to Telegram chat
        │
        ▼
User receives processed video in Telegram
```

### Agent Headless Runs

For AI agent tasks (code changes, not video processing), the flow is:

```
Telegram: /agent <task description>
  → cloudflare-worker/worker.js: handleAgentCommand() (auth-checked against
    env.AGENT_CHAT_ID, fails closed if unset)
  → repository_dispatch type: agent_command, event fired to GitHub
  → .github/workflows/agent-task.yml (installed from docs/agent-task.yml)
  → creates branch agent/<slug>-<run-id>
  → `grok` CLI runs headlessly with repository context:
    `grok agent run --headless --yes --prompt "$COMMAND"`
  → pre-commit checklist runs automatically before commit
  → commit, push branch, gh pr create against main
  → Telegram: "✅ Done — tap to review & merge: <PR URL>"
```

See `.agent/QUICKSTART.md` for the one-time manual setup this requires
(installing the workflow file, adding the `GROK_AUTH_JSON` secret created
from `~/.grok/auth.json`, and the Worker's `AGENT_CHAT_ID` secret) and the
full skill-file routing table.

## Secrets Reference (GitHub Actions)

| Secret | Used By | Purpose |
|--------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | telegram-dispatch, agent-task | Bot API authentication |
| `TELEGRAM_API_ID` | telegram-dispatch (LofiLoop) | MTProto app credentials |
| `TELEGRAM_API_HASH` | telegram-dispatch (LofiLoop) | MTProto app credentials |
| `GITHUB_TOKEN` | Cloudflare Worker | Trigger repository_dispatch |

## Session Checklist for Agents

Before starting work:
1. Read this file (`AGENTS.md`)
2. Read `.agent/MEMORY.md` for persistent decisions and known issues
3. Check `.agent/skills/` for a matching skill file if the task is a common type
   (see the routing table in `.agent/QUICKSTART.md` — 14 skill files cover
   bug fixes, features, new tools, new bot commands, refactors, dependency
   bumps, security audits, CI/CD recovery, performance, rollback, tests, and
   docs sync)
4. Run `git status` to confirm clean working tree
5. Create a branch or use `genspark_ai_developer` for changes
6. Before opening a PR, run `.agent/skills/security-audit.md`'s checklist —
   it's a mandatory gate, not optional
