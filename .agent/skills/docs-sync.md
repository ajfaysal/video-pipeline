# Skill: Docs Sync

> **Trigger**: "update docs", "sync README", "docs are out of date", "CLI flags changed"

## Procedure

### 1. Find every place a CLI flag / tool fact is documented
For any tool whose `argparse` flags changed, these locations must all agree:

| Location | What to check |
|----------|---------------|
| `README.md` — tool's numbered section | Usage code blocks show real, current flags |
| `AGENTS.md` — Tools table | One-line purpose still accurate |
| `bot/run_job.py` — module docstring | Env var list matches every `_env()` call |
| `bot/run_job.py` — `run_<tool>()` | `cmd = [...]` list matches the tool's actual argparse flags exactly |
| `.github/workflows/<tool>.yml` or `docs/<tool>.workflow.yml` | `workflow_dispatch.inputs` match the CLI flags |
| `.github/workflows/telegram-dispatch.yml` (or `docs/` copy) | `env:` block passthrough matches `options.*` keys the Worker sends |
| `cloudflare-worker/worker.js` — `dispatchAndFinish()` `options: {...}` | Every option the tool needs is present with a sane default |

### 2. Grep-verify, don't eyeball
```bash
# Extract the actual argparse flags for a tool:
grep -n "add_argument" <tool>/main.py

# Extract what README shows for that tool's usage block:
sed -n '/## N\. ToolName/,/^## N+1\./p' README.md | grep '^python'

# Extract what bot/run_job.py actually passes:
sed -n '/def run_<tool>/,/^def /p' bot/run_job.py | grep -E '"--|cmd \+='
```
Any flag present in one place and missing/renamed in another is the bug to
fix — this is the single most common source of "the bot says it worked but
nothing happened" reports.

### 3. Update in this order (so nothing gets missed)
1. Confirm the actual current code behavior (source of truth = `argparse`
   definitions in `<tool>/main.py`).
2. Update `bot/run_job.py`'s docstring + `run_<tool>()` to match.
3. Update the workflow YAML env/input mapping to match.
4. Update `cloudflare-worker/worker.js` if the Telegram flow needs a new
   question/option.
5. Update `README.md`'s usage block and bullet list last, once code is
   confirmed correct — docs should describe reality, never the reverse.
6. Update `AGENTS.md`'s Tools table only if the tool's *purpose* changed,
   not for every flag tweak (keep AGENTS.md high-level).

### 4. Markdown hygiene (per `.agent/hooks/post-edit-checklist.md`)
- Headers, code blocks, and tables must render correctly — check fenced code
  blocks are closed, table pipes align conceptually (exact spacing doesn't
  matter, column count does).
- Code examples must be copy-pasteable and runnable as shown — no
  placeholder flags that don't actually exist.

### 5. Verify
```bash
python <tool>/main.py --help    # compare output against the README block by eye
grep -c '```' README.md         # even number = all code fences closed
```

### 6. Commit
`docs(<tool>): sync README/AGENTS.md with current CLI flags`

## Example

**Input**: "I added a --speed flag to AspectShift last week but forgot the docs"

**Output**:
```
Found the gap: aspectshift/main.py has --speed (added in commit abc123) but:
  - README.md's AspectShift section usage block didn't show it
  - bot/run_job.py's docstring didn't list SPEED env var (the code did read it — just undocumented)
  - telegram-dispatch.yml env: block WAS already passing SPEED through (lucky — no functional bug, just doc gap)

Files changed:
  README.md      — added --speed to the usage example + one bullet describing the range/default
  bot/run_job.py — added SPEED to the module docstring's env var list

Commit: docs(aspectshift): document --speed flag in README and run_job.py docstring
```
