# Skill: Add Feature

> **Trigger**: "add feature", "implement", "new tool", "support for", "add option"

## Procedure

### 1. Plan
- Clarify what the feature does and which tool it belongs to
- Check `AGENTS.md` for coding standards and naming conventions
- Check `.agent/MEMORY.md` for any relevant past decisions
- Identify all files that need changes (tool code, `bot/run_job.py`, workflow YAML, `README.md`)

### 2. Implement
- Follow existing patterns in the codebase:
  - CLI args via `argparse` with `--output-dir`
  - Tool logic in `<tool>/main.py` or helper modules
  - Bot integration in `bot/run_job.py` (new `run_<tool>()` function + entry in `TOOL_RUNNERS`)
  - Workflow integration in `.github/workflows/` (new or updated YAML)
- Use type hints, `from __future__ import annotations`
- Default to `libx264 -crf 16 -preset slow` for quality encoding

### 3. Test
- Run `python <tool>/main.py --help` to verify CLI loads
- Run any existing tests/lint
- Verify `bot/run_job.py` still imports cleanly: `python -c "import bot.run_job"`

### 4. Document
- Update `README.md` with usage examples for the new feature
- Add a section matching the existing format (numbered heading, code blocks, known limitations)

### 5. Commit
- `feat(<tool>): short description`
- If multiple files changed, list the key changes in the commit body

## Example

**Input**: "Add a --speed flag to AspectShift that lets users speed up or slow down the output"

**Output**:
```
Files changed:
  aspectshift/converter.py  — added speed_factor param to convert()
  aspectshift/main.py       — added --speed argparse flag
  bot/run_job.py            — reads SPEED env var in run_aspectshift()
  README.md                 — documented --speed under AspectShift section

Commit: feat(aspectshift): add --speed flag for playback speed control
```
