# Skill: Debug Pipeline (GitHub Actions / Cloudflare Worker Failures)

> **Trigger**: "action failed", "workflow error", "worker broken", "pipeline fail", "CI error", "dispatch not working"

## Procedure

### 1. Identify the Failure Layer

```
Telegram message
  → Cloudflare Worker (worker.js)     ← JS runtime errors, KV issues
    → GitHub Actions dispatch          ← 404/422 = bad token or wrong event_type
      → telegram-dispatch.yml          ← YAML syntax, missing secrets, bad env mapping
        → bot/run_job.py               ← Python import errors, missing env vars
          → <tool>/main.py             ← ffmpeg errors, file not found, logic bugs
            → Telegram reply            ← Bot API errors, file too large
```

### 2. Read the Logs
- **GitHub Actions**: check the run log for the failing step — the step name tells you which layer
- **Cloudflare Worker**: `wrangler tail` or check the Worker dashboard for errors
- **Telegram**: if the bot sent an error message, it came from `bot/run_job.py`'s except block

### 3. Common Failure Patterns

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Bot doesn't respond at all | Worker crash or webhook not set | Check `wrangler tail`, re-set webhook |
| "Job started" but no result | GitHub Action failed mid-run | Check Actions log for ffmpeg/Python error |
| "Unknown tool 'X'" | Worker sent wrong tool name | Check `TOOL_RUNNERS` dict in `run_job.py` |
| 404 on dispatch | Bad `GITHUB_TOKEN` or `GITHUB_REPO` secret in Worker | Re-set wrangler secrets |
| "Missing required environment variable" | `telegram-dispatch.yml` env block incomplete | Add missing env var mapping |
| ffmpeg "No such file or directory" | Download step failed silently | Check `yt-dlp` / `gdown` output in logs |
| Telegram send fails (413) | Output file exceeds 50 MB Bot API limit | LofiLoop uses MTProto; other tools need artifact fallback |

### 4. Isolate the Failing Step
- Find the **first red step** in the Actions log
- Read the full error output for that step
- Reproduce locally if possible: `python bot/run_job.py` with env vars set manually

### 5. Propose Minimal Fix
- Target only the failing component
- If the fix is in a workflow YAML, validate syntax before committing
- If the fix is in `worker.js`, test with `wrangler dev` if possible
- If the fix is in Python, verify with `--help` and import checks

### 6. Commit
- `fix(ci): short description` for workflow fixes
- `fix(worker): short description` for Cloudflare Worker fixes
- `fix(<tool>): short description` for tool-level fixes

## Example

**Input**: "The AudioDuck action is failing with 'VOICEOVER_SOURCE required'"

**Output**:
```
Root cause: telegram-dispatch.yml was missing the VOICEOVER_SOURCE env mapping.
The Worker sends it in client_payload.options.voiceover_source but the workflow
didn't pass it through to the runner environment.

Fix: Added this line to telegram-dispatch.yml env block:
  VOICEOVER_SOURCE: ${{ github.event.client_payload.options.voiceover_source }}

Commit: fix(ci): add missing VOICEOVER_SOURCE env mapping in telegram-dispatch
```
