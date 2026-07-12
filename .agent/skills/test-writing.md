# Skill: Test Writing

> **Trigger**: "add tests", "write tests", "test this", "make sure it's tested"

## Context: this repo currently has no formal test suite

`.agent/MEMORY.md` documents this explicitly: validation today is manual
(`--help` + import checks). Don't invent a heavyweight testing framework in
one PR — introduce the minimum viable structure and grow it incrementally.

## Procedure

### 1. Choose the lightest tool that fits
- Prefer stdlib `unittest` or a minimal `pytest` addition
  (`pip install pytest`, add to `requirements.txt` under a new
  `# Testing` section) over anything heavier.
- For pure-logic functions (no ffmpeg/network/whisper calls), a plain
  `unittest.TestCase` with no fixtures is enough.
- For CLI entry points, at minimum assert `--help` exits 0 and imports work
  — this repo's existing bar (`deploy-check.md`'s "Quick One-Liner Check")
  is already a real (if minimal) test; formalize it rather than replacing it.

### 2. Scaffold the first test file for a tool
```
tests/
└── test_<tool>.py
```
```python
"""Minimal smoke + logic tests for <tool>. Run with: python -m pytest tests/"""
from __future__ import annotations

import subprocess
import sys


def test_cli_help_loads():
    result = subprocess.run(
        [sys.executable, "<tool>/main.py", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()


def test_pure_logic_function():
    from <tool>.main import some_pure_function  # only if such a function exists
    assert some_pure_function(known_input) == expected_output
```

### 3. Prioritize what to test, in this order
1. **Pure functions with no I/O** (scoring math, filename parsing, argparse
   validation) — cheapest, most valuable, no fixtures needed.
2. **CLI contract** (`--help` exits 0, required-arg validation raises the
   right error) — catches accidental flag renames that would silently break
   `bot/run_job.py` or workflow YAML mappings.
3. **`bot/run_job.py` env-var contract** — a test that sets minimal required
   env vars and confirms `_env(..., required=True)` raises when missing,
   without actually invoking ffmpeg.
4. Skip testing raw ffmpeg output correctness in CI unless a small sample
   fixture video already exists in the repo — don't commit large binary
   fixtures without being asked.

### 4. Never let "add tests" block a small fix
If the task is primarily a bug fix or small feature and the user separately
asked for tests, write **one** focused test that would have caught the
specific bug/regression, then stop — don't scope-creep into a full suite in
an unrelated PR (violates AGENTS.md rule 6: one logical change per commit).

### 5. Verify
```bash
python -m pytest tests/ -v
# or, if pytest isn't added yet and the task is small:
python -m unittest discover tests/
```

### 6. Commit
`test(<scope>): add <what> tests`

## Example

**Input**: "Add a test that catches the ClipHarvest no-audio crash we just fixed"

**Output**:
```
Files created:
  tests/test_clipharvest.py — test_scorer_handles_silent_video() constructs
    an empty-audio-array input and asserts scorer.energy_score() returns 50
    (the neutral fallback) instead of raising KeyError.

Verified: python -m pytest tests/test_clipharvest.py -v  ->  1 passed

Commit: test(clipharvest): add regression test for no-audio KeyError fix
```
