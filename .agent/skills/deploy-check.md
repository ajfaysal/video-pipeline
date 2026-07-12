# Skill: Deploy Check (Pre-Deploy Checklist)

> **Trigger**: "deploy", "pre-deploy", "release check", "ready to ship", "production check"

## Procedure

### 1. Lint & Syntax
- [ ] `python -m py_compile <changed_files>` — no syntax errors
- [ ] Run any configured linter (flake8, ruff, etc.) if present
- [ ] Check for `print()` debug statements that should be removed

### 2. Build & Import Verification
- [ ] `pip install -r requirements.txt` succeeds
- [ ] `python -c "import bot.run_job"` loads without errors
- [ ] Each tool loads: `python <tool>/main.py --help` for all 10 tools

### 3. Environment & Secrets
- [ ] No hardcoded tokens, API keys, or hashes in tracked files
- [ ] `grep -r "sk-\|ghp_\|Bearer \|password" --include="*.py" --include="*.js"` returns nothing suspicious
- [ ] `.env` is in `.gitignore`
- [ ] All required GitHub Actions secrets are documented in `AGENTS.md`

### 4. Workflow Validation
- [ ] YAML syntax is valid: `python -c "import yaml; yaml.safe_load(open('<file>'))"` for changed workflows
- [ ] `telegram-dispatch.yml` env block matches all env vars read by `bot/run_job.py`
- [ ] `wrangler.toml` KV namespace ID hasn't been accidentally changed

### 5. Backward Compatibility
- [ ] No CLI flag renames (would break `workflow_dispatch` inputs)
- [ ] No env var renames (would break `telegram-dispatch.yml` payload mapping)
- [ ] No removed exports used by `bot/run_job.py`

### 6. Git Hygiene
- [ ] Clean working tree: `git status` shows nothing untracked that should be committed
- [ ] Commit messages follow `type: description` format
- [ ] No merge conflict markers in any file

## Quick One-Liner Check

```bash
# Run all import checks at once
for tool in aspectshift clipharvest watermarkwipe abroll introoutro stitcher audioduck loudnorm autochapters lofiloop; do
  python -c "import ${tool}.main" && echo "OK: $tool" || echo "FAIL: $tool"
done
python -c "import bot.run_job" && echo "OK: bot.run_job" || echo "FAIL: bot.run_job"
```

## Example

**Input**: "Check if the repo is ready to deploy after the latest changes"

**Output**:
```
Deploy check results:
  ✅ All 10 tools import cleanly
  ✅ bot/run_job.py loads without errors
  ✅ No secrets found in tracked files
  ✅ .env is gitignored
  ⚠️  telegram-dispatch.yml is missing NEW_ENV_VAR that run_job.py now reads
  → Action: add NEW_ENV_VAR to the workflow env block
```
