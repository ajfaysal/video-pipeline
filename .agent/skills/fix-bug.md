# Skill: Fix Bug

> **Trigger**: "fix bug", "debug", "broken", "error in", "not working", "crash"

## Procedure

### 1. Reproduce
- Read the bug report / error message carefully
- Identify the affected tool (check tool folder + `bot/run_job.py`)
- If a traceback is provided, locate the exact file and line
- Run `python <tool>/main.py --help` to confirm the module loads without import errors

### 2. Isolate
- Trace the call chain: Telegram → `bot/run_job.py` → `<tool>/main.py` → helper modules
- Check if the bug is in Python logic, an ffmpeg command, or a workflow env var mismatch
- Look for recent commits that may have introduced the regression

### 3. Patch
- Make the minimal change that fixes the root cause
- Preserve backward compatibility with existing CLI args and env var interfaces
- Add a comment explaining *why* the fix works if it's non-obvious

### 4. Verify
- Run `python <tool>/main.py --help` to confirm no import errors
- If a test suite exists, run it
- If the fix involves ffmpeg, test with a short sample command if possible

### 5. Commit
- `fix(<tool>): short description of what was broken and how it's fixed`
- Reference the issue number or Telegram error message if available

## Example

**Input**: "ClipHarvest crashes with KeyError on videos without audio"

**Output**:
```
fix(clipharvest): handle videos with no audio stream in scorer

Added a check for empty audio arrays before computing energy scores.
Videos without audio now receive a neutral energy score of 50 instead
of crashing with KeyError.
```
