# Skill: CI/CD Recovery

> **Trigger**: "GitHub Action failed", "worker deploy failed", "self-heal", "pipeline broken", "workflow crashed"

This skill is a superset of `.agent/skills/debug-pipeline.md` focused specifically
on *recovering* — not just diagnosing — a failed Action run or Worker deploy,
including when to self-heal automatically vs. when to stop and report.

## Procedure

### 1. Pull the actual failure, don't guess
```bash
gh run list --limit 5
gh run view <run-id> --log-failed
```
Read the **first** failing step's full output — later steps often fail as a
downstream symptom, not the root cause.

### 2. Classify the failure

| Class | Signal | Self-heal? |
|-------|--------|-----------|
| Transient infra (GitHub 5xx, runner OOM, network blip) | Error mentions 502/503, "runner lost communication", timeout with no app-level error | Yes — re-run: `gh run rerun <run-id> --failed` |
| Missing/misnamed secret | "Missing required environment variable: X" or empty token in a curl call | Yes if the fix is a code/workflow mapping bug (add the env passthrough); **No** if the secret genuinely doesn't exist in repo settings — report exactly which secret name is missing |
| Dependency install failure | pip/npm resolution error, version conflict | Yes — usually a `requirements.txt` pin issue; apply `.agent/skills/dependency-update.md` |
| YAML/syntax error in a workflow | "yaml: line X: did not find expected..." | Yes — fix the workflow file directly (unless it's in `.github/workflows/` and blocked by the GitHub App's `workflows` permission — then fix the `docs/` copy and note the manual re-install step) |
| Application logic bug (ffmpeg args, Python exception) | Traceback pointing at repo code | Yes — apply `.agent/skills/fix-bug.md` |
| Cloudflare Worker deploy failure | `wrangler deploy` non-zero exit, KV namespace mismatch, missing secret | Partial — code-level fixes yes; if it needs `wrangler secret put` or a Cloudflare dashboard action, that's a credential the agent structurally cannot have — report precisely |
| Branch protection / permissions rejection | "refusing to allow a GitHub App to create or update workflow", "protected branch hook declined" | **No** — report precisely; do not retry in a loop |

### 3. Self-heal loop (bounded)
- Make the minimal targeted fix for the classified root cause.
- Re-run **once**: `gh run rerun <run-id> --failed` or push the fix and let
  the workflow re-trigger naturally.
- If it fails again with a **different** error, treat it as a new root cause
  and repeat step 1-2 (fresh diagnosis, don't just retry blindly).
- If it fails again with the **same** error after one fix attempt, stop
  self-healing and report — looping on an unfixable error wastes CI minutes
  and Actions quota.

### 4. Cloudflare Worker specific recovery
```bash
cd cloudflare-worker
node -c worker.js                       # syntax check first, cheapest signal
npx wrangler deploy --dry-run 2>&1 || true   # if wrangler CLI is available
```
- KV namespace ID mismatch: compare `wrangler.toml`'s `id` against
  `wrangler kv namespace list` output — never invent a new ID, that silently
  orphans existing bot conversation state.
- If the failure is "Authentication error" / missing `CLOUDFLARE_API_TOKEN`,
  this is a credential the agent cannot obtain — report exactly which secret
  is missing and where to add it (repo secrets, per README's Setup section).

### 5. When to stop and report precisely (never vaguely)
Report immediately, with the exact error and exact missing item, when:
- A GitHub secret referenced by a workflow doesn't exist in repo settings.
- A Cloudflare API token/account ID is missing or invalid.
- Branch protection rejects a push/PR in a way that requires a repo admin
  to change settings.
- The same root-cause error persists after one self-heal attempt.

Bad report: "CI is broken, please check."
Good report: "telegram-dispatch.yml run #482 failed at step 'Run requested
tool' with `RuntimeError: Missing required environment variable:
VOICEOVER_SOURCE`. The workflow's env: block is missing the
`options.voiceover_source` mapping that bot/run_job.py now reads. Fixing by
adding that one line — no credential needed."

## Example

**Input**: "The autochapters GitHub Action keeps failing"

**Output**:
```
Diagnosis: run #517, step "Run requested tool and reply on Telegram" failed
with ModuleNotFoundError: No module named 'faster_whisper'. Root cause:
requirements.txt cache key changed but the cache step didn't invalidate
correctly — actually a pip install silently no-op'd because of a stale
~/.cache/pip hit from before faster-whisper was added.

Self-heal: bumped the pip cache key in telegram-dispatch.yml (docs/ copy,
since .github/workflows/ is write-blocked) to include requirements.txt hash
(it already does — the real issue was a corrupted cache entry). Manually
triggered gh run rerun with cache-bypass by incrementing the key suffix.

Re-ran: success on retry.

Commit: fix(ci): bust stale pip cache key that hid faster-whisper install
```
