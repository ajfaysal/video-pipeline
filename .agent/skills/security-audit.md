# Skill: Security Audit

> **Trigger**: "security audit", "scan for secrets", "check permissions", "before every PR", "exposed token"

## Procedure

Run this before opening **every** PR the agent creates, not just when
explicitly asked — treat it as a mandatory gate alongside
`.agent/hooks/pre-commit-checklist.md`.

### 1. Hardcoded secrets scan
```bash
# Literal-looking secrets in the diff (not variable references):
git diff --cached | grep -inE "(sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{10,}|gho_[A-Za-z0-9]{10,}|AIza[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._-]{10,})"

# Anything that looks like a bare token assignment (not ${{ ... }} / ${...} / os.environ):
git diff --cached | grep -inE '(api_key|api_hash|token|secret|password)\s*[=:]\s*["\x27][A-Za-z0-9+/=_-]{12,}["\x27]' \
  | grep -v '\${{' | grep -v '\${' | grep -v 'os\.environ' | grep -v 'PLACEHOLDER'
```
Any match must be either a `${PLACEHOLDER}` / `${{ secrets.X }}` / `os.environ.get(...)`
reference, or removed entirely. There is one known, intentional exception:
`bot/mtproto_transfer.py` ships baked-in default MTProto API id/hash values
documented in `docs/WORKFLOW_CHANGES_REQUIRED.md` as a deliberate zero-config
convenience — do not flag or "fix" those unless the task is specifically about
rotating them.

### 2. `.env` and secret-file hygiene
```bash
git diff --cached --name-only | grep -v '\.gitignore' | grep '\.env'   # must be empty
cat .gitignore | grep -q '^\.env$' && echo "OK: .env is gitignored"
```

### 3. Workflow permissions review
For any `.github/workflows/*.yml` or `docs/*.yml` (workflow drafts) touched:
- Confirm `permissions:` is scoped to the minimum needed
  (`contents: write` + `pull-requests: write` for agent-task.yml-style PR
  automation; plain jobs that only read/run don't need `contents: write`).
- Flag (don't silently "fix") any workflow requesting `contents: write` with
  no commit/push step, or any workflow that checks out with a token that has
  broader scope than `secrets.GITHUB_TOKEN` without a stated reason.
- Confirm no workflow echoes a secret to logs (`echo ${{ secrets.X }}` is a
  leak even inside `run:` — GitHub masks *known* secret values but not
  derived/partial ones like `${TOKEN:-4}`).

### 4. Cloudflare Worker secret handling
- `cloudflare-worker/worker.js` must never contain a literal token — only
  `env.TELEGRAM_BOT_TOKEN`, `env.GITHUB_TOKEN`, etc. read from Worker
  secrets/bindings.
- Any new authorization check (like `AGENT_CHAT_ID`) must **fail closed**:
  if the secret/env var is unset, the feature must refuse access, never
  default to "allow everyone".

### 5. Dependency/supply-chain spot-check
- If `requirements.txt` changed, confirm the package name wasn't typo-squatted
  (e.g. `python-opencv` vs `opencv-python-headless`) — check it resolves on
  PyPI and matches the intended, well-known package.

### 6. Report format
Always report findings as a checklist, not prose:
```
Security audit:
  ✅ No hardcoded secrets in diff
  ✅ .env is gitignored, not staged
  ✅ New workflow permissions scoped to contents:write, pull-requests:write only
  ⚠️  worker.js: new AUTHOR_CHAT check defaults to "allow" when env var unset — FIX before merge
```
If any ⚠️/❌ item exists, fix it and re-run this checklist before calling `finish`.

## Example

**Input**: "Security audit before merging the /agent command PR"

**Output**:
```
✅ dispatchAgentTask() reads env.GITHUB_TOKEN / env.GITHUB_REPO, no literals
✅ handleAgentCommand() fails closed when AGENT_CHAT_ID is unset
✅ agent-task.yml permissions scoped to contents:write, pull-requests:write
✅ No secrets echoed in any new run: block
✅ agent_runner.py write_file() blocks .git/ and .github/workflows/ paths
Result: clean, safe to merge.
```
