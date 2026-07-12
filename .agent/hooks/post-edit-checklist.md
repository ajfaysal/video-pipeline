# Post-Edit Checklist

> Run through this checklist after editing any file, before staging the change.

## After Editing Python Files

- [ ] **Imports still valid**: no removed imports that other files depend on
- [ ] **No circular imports**: if you moved code between modules, verify both sides import cleanly
- [ ] **No orphaned code**: if you removed a function, grep for all callers: `grep -r "function_name" --include="*.py"`
- [ ] **Type hints present**: new functions have parameter and return type hints
- [ ] **Error handling**: new code raises `RuntimeError` with descriptive messages (caught by `bot/run_job.py`)

## After Editing bot/run_job.py

- [ ] **TOOL_RUNNERS dict updated**: if you added/renamed a tool, update the dict at the bottom
- [ ] **Env var documented**: any new `_env()` call must have a matching entry in the docstring at the top of the file
- [ ] **Env var mapped in workflow**: new env vars must also be added to `telegram-dispatch.yml`'s env block

## After Editing Workflow YAML

- [ ] **YAML syntax valid**: `python -c "import yaml; yaml.safe_load(open('.github/workflows/<file>'))"` 
- [ ] **Env vars match**: every env var in the workflow has a corresponding `_env()` call in `bot/run_job.py`
- [ ] **Action versions current**: prefer `@v4` for checkout, setup-python, upload-artifact, cache

## After Editing worker.js

- [ ] **No syntax errors**: `node -c cloudflare-worker/worker.js`
- [ ] **KV namespace ID unchanged**: verify `wrangler.toml` still has the correct ID
- [ ] **Tool names match**: tool names sent in dispatch payload must match `TOOL_RUNNERS` keys

## After Editing README.md

- [ ] **Code examples accurate**: CLI commands in docs match actual argparse flags
- [ ] **Tool list complete**: all tools are listed in both the intro table and their own section
- [ ] **No broken markdown**: headers, code blocks, and tables render correctly
