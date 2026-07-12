# Pre-Commit Checklist

> Run through this checklist before every `git commit`. If any item fails,
> fix it before committing.

## Mandatory Checks

- [ ] **No secrets in diff**: `git diff --cached | grep -iE "(sk-|ghp_|token|password|secret|api_key|api_hash)" ` — review any matches, ensure they're variable references not literal values
- [ ] **No .env committed**: `git diff --cached --name-only | grep -v ".gitignore" | grep "\.env"` returns nothing
- [ ] **Python syntax valid**: `python -m py_compile <changed .py files>`
- [ ] **Imports clean**: `python -c "import bot.run_job"` succeeds
- [ ] **Affected tool loads**: `python <tool>/main.py --help` for any tool you modified
- [ ] **Commit message format**: `type: short description` (types: feat, fix, refactor, ci, docs, chore, agent)
- [ ] **No debug leftovers**: no stray `print("DEBUG")`, `breakpoint()`, `import pdb` in the diff

## Conditional Checks

- [ ] **If tests exist**: run them and confirm pass
- [ ] **If linter is configured**: run it and confirm clean
- [ ] **If workflow YAML changed**: validate syntax with `python -c "import yaml; yaml.safe_load(open('path'))"`
- [ ] **If requirements.txt changed**: `pip install -r requirements.txt` still works

## Quick Script

```bash
# Paste into terminal before committing
echo "=== Secrets check ===" && \
git diff --cached | grep -inE "(sk-|ghp_|Bearer |password=|api_key=)" || echo "Clean" && \
echo "=== Import check ===" && \
python -c "import bot.run_job" && echo "OK" || echo "FAIL" && \
echo "=== Syntax check ===" && \
git diff --cached --name-only --diff-filter=AM | grep '\.py$' | xargs -I{} python -m py_compile {} && echo "All syntax OK"
```
