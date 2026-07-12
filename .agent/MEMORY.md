# Agent Memory — Persistent Decisions & Context

> This file is a running log of architectural decisions, library choices,
> known issues, and workarounds. Every agent session should read this file
> at startup and append new entries at the bottom when making significant
> decisions. Use the format below.

---

## Entry Format

```
### YYYY-MM-DD — Short Title
**Decision**: What was decided
**Reasoning**: Why
**Affects**: Which files/tools
**Status**: Active | Superseded by <entry> | Resolved
```

---

### 2026-07-11 — Initial Repo State Snapshot

**Decision**: Documented the baseline state of the repository for future agent sessions.

**Current state**:
- 10 video-processing CLI tools, all functional, each in its own folder with `main.py`
- Telegram bot flow: Cloudflare Worker (`cloudflare-worker/worker.js`) → GitHub Actions (`telegram-dispatch.yml`) → `bot/run_job.py` → tool
- 11 GitHub Actions workflows: 1 per tool (workflow_dispatch) + 1 telegram-dispatch (repository_dispatch)
- Python 3.11, ffmpeg-based processing, faster-whisper for transcription
- LofiLoop supports 2 GB delivery via MTProto (pyrogram) with monetization-safe fingerprinting
- No formal test suite exists — validation is manual (`--help` + import checks)
- No linter config exists (no `.flake8`, `pyproject.toml[tool.ruff]`, etc.)
- `.env` is gitignored; secrets live in GitHub Actions secrets + Cloudflare Worker secrets
- `docs/WORKFLOW_CHANGES_REQUIRED.md` contains manual steps for lofiloop workflow that may or may not have been applied yet

**Known issues**:
- WatermarkWipe inpaint mode has quality limits on complex backgrounds (documented in README)
- ABRoll cut detection is heuristic and may need per-video threshold tuning
- IntroOutro text rendering can get cramped with long strings
- Telegram Bot API file limit: 20 MB receive / 50 MB send (MTProto raises send to 2 GB)

**Affects**: Entire repo
**Status**: Active

---

### 2026-07-11 — Agent Infrastructure Setup

**Decision**: Created `.agent/` directory with skills, hooks, memory, and quickstart. Created `AGENTS.md` at repo root. Added `agent-task.yml` workflow for headless agent runs via repository_dispatch. Added `.mcp/config.json` template.

**Reasoning**: Enable any future agent session (Grok Build, Claude Code, etc.) to instantly understand repo conventions and reuse standard procedures without re-explanation.

**Affects**: New files only — no existing code modified
**Status**: Active

---

### 2026-07-11 — Wired Telegram `/agent` command to a real headless coding agent

**Decision**: Implemented the actual end-to-end path from Telegram to a code
change PR, not just the scaffolding:
1. Added `handleAgentCommand()` + `dispatchAgentTask()` to
   `cloudflare-worker/worker.js`. Checked first in `handleMessage()`,
   returns immediately — verified zero existing branches (video tool menu,
   LofiLoop multi-step flow, ABRoll/Stitcher/AudioDuck collection steps) were
   touched. Auth via new `env.AGENT_CHAT_ID` secret, fails closed if unset.
   Uses a distinct `repository_dispatch` `event_type: "agent_command"` —
   never reuses `"telegram-job"` (the video-tool event type), so the two
   dispatch paths can never cross-talk.
2. Wrote `bot/agent_runner.py` — the actual agent. Runs a bounded (18-turn
   default) LLM tool-use loop against an OpenAI-compatible chat completions
   API (`OPENAI_API_KEY`/`OPENAI_BASE_URL` env vars, model default
   `gpt-5-codex`, overridable via `AGENT_MODEL` repo variable). Tools:
   `list_files`, `read_file`, `write_file` (blocked from `.git/` and
   `.github/workflows/`), `run_command` (blocklist for `git commit`/`git
   push`/network fetches — those stay in the calling workflow, not the
   model's hands), `finish`, `give_up`. Automatically loads `AGENTS.md` +
   `.agent/MEMORY.md` + a keyword-matched `.agent/skills/*.md` file into the
   system prompt. Runs the pre-commit checklist (syntax/YAML/JS syntax +
   secrets regex scan) before accepting `finish` — rejects and feeds
   problems back into the loop if anything fails.
3. Rewrote `docs/agent-task.yml` (still can't be pushed directly to
   `.github/workflows/` — confirmed again by testing a direct push, same
   "GitHub App... without `workflows` permission" rejection as the original
   PR #4). New version: creates a uniquely-named `agent/<slug>-<run-id>`
   branch, runs `agent_runner.py`, commits with the model's own
   commit-message, pushes, opens a PR via `gh pr create`, and sends distinct
   Telegram messages for every failure mode (dependency install failure,
   agent give-up/failure with the exact reason extracted from stdout, no
   changes needed, push failure, PR-creation failure, success with the PR
   link) — never a generic "something went wrong".
4. Added 9 new skill files rounding out the library to 14 total:
   `add-telegram-command.md`, `new-tool-scaffold.md`,
   `dependency-update.md`, `security-audit.md`, `ci-cd-recovery.md`,
   `performance-check.md`, `rollback.md`, `test-writing.md`,
   `docs-sync.md`. Updated `.agent/QUICKSTART.md`'s skill-routing table
   (also implemented as `_pick_skill_file()` in `agent_runner.py` so the
   routing is live code, not just documentation).
5. Added `openai>=1.40.0` and `pyyaml>=6.0.1` to `requirements.txt`.

**Reasoning**: The prior state only had a placeholder workflow (`echo "TODO:
Replace this with actual agent CLI invocation"`) with no real Telegram
trigger and no LLM in the loop. This closes that gap end-to-end while
keeping git commit/push authority entirely inside the workflow (never inside
the model's tool-calling reach) as a safety boundary.

**Known limitation / manual step still required**: `.github/workflows/agent-task.yml`
cannot be installed by this agent session — confirmed blocked by the
GitHub App's missing `workflows` permission (same constraint documented for
`docs/lofiloop.workflow.yml` in `docs/WORKFLOW_CHANGES_REQUIRED.md`). A
human with normal push access must run the one-time `cp docs/agent-task.yml
.github/workflows/agent-task.yml && git add && git commit && git push`
sequence documented in `.agent/QUICKSTART.md`. Until that's done, `/agent`
will dispatch successfully but no workflow will pick up the event.

**Affects**: `cloudflare-worker/worker.js` (additive only), new file
`bot/agent_runner.py`, `docs/agent-task.yml` (rewritten), `requirements.txt`,
`.agent/skills/*.md` (9 new files), `.agent/QUICKSTART.md`, `AGENTS.md`
**Status**: Active — code complete and self-tested (syntax/YAML/import
checks all pass); blocked only on the one manual workflow-install step and
the three one-time secrets (`OPENAI_API_KEY`, `OPENAI_BASE_URL`,
`AGENT_CHAT_ID`) listed in `.agent/QUICKSTART.md`.
