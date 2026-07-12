# Skill: Refactor

> **Trigger**: "refactor", "clean up", "reorganize", "simplify", "reduce duplication"

## Procedure

### 1. Scope
- Identify what needs refactoring and why (duplication, complexity, readability)
- Confirm the refactor is **behavior-preserving** — no new features, no removed features
- List all files in the blast radius

### 2. Baseline
- Run `python <tool>/main.py --help` for each affected tool to capture current behavior
- Run existing tests/lint if available
- Note the current git state: `git log --oneline -5`

### 3. Refactor
- Make incremental changes, not sweeping rewrites
- Common patterns in this repo:
  - Extract shared ffmpeg command builders into a utility module
  - Deduplicate download logic (yt-dlp / URL handling) across tools
  - Consolidate `_env()` / `_run()` patterns already in `bot/run_job.py`
- Preserve all public interfaces: CLI args, env var names, function signatures used by `bot/run_job.py`

### 4. Verify
- Run the same `--help` commands and confirm identical output
- Run existing tests/lint
- `python -c "import bot.run_job"` must still succeed
- Diff the changes: `git diff --stat` should show no new public API changes

### 5. Commit
- `refactor(<scope>): short description`
- Commit message body should explain *what was consolidated* and *why*

## Anti-Patterns (do NOT)

- Do not rename CLI flags (breaks existing workflows and Telegram bot)
- Do not change env var names in `run_job.py` (breaks `telegram-dispatch.yml`)
- Do not move `main.py` out of its tool folder (breaks workflow `run:` commands)
- Do not combine multiple tools into one file

## Example

**Input**: "Deduplicate the URL download logic that appears in every tool"

**Output**:
```
refactor(shared): extract common download_source() into shared/downloader.py

Moved the yt-dlp/URL/path resolution logic used by 6 tools into a shared
helper. Each tool's main.py now calls download_source() instead of
duplicating the same 15 lines. No behavior change — all CLI args and
outputs are identical.
```
