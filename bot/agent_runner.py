"""
agent_runner.py
---------------
Headless coding agent invoked by `.github/workflows/agent-task.yml` (or its
one-time-manual-install counterpart `docs/agent-task.yml`) whenever a
`repository_dispatch` event of type `agent_command` arrives. That event is
fired by the Telegram bot's `/agent <task description>` command via
`cloudflare-worker/worker.js` -> `dispatchAgentTask()`.

Given a natural-language task description, this script:
  1. Loads repo context the same way a human agent session would
     (AGENTS.md, .agent/MEMORY.md, the best-matching `.agent/skills/*.md`
     file for the task).
  2. Runs a bounded tool-use loop against an OpenAI-compatible chat
     completions API. The model can call `list_files`, `read_file`,
     `write_file`, and `run_command` to inspect and edit the repository,
     then call `finish` when done (or `give_up` with a precise reason).
  3. Runs the repo's own pre-commit checklist automatically (syntax checks,
     import checks, YAML validation, `node -c` on worker.js) after the model
     finishes, and feeds any failures back into the loop so the model can
     self-correct before the workflow commits anything.
  4. Leaves the working tree with the (uncommitted) changes on disk. Git
     branch/commit/push/PR creation stays in the calling GitHub Actions
     workflow so the GitHub credential only lives in one place.

This file intentionally does NOT commit, push, or touch git state directly —
that keeps the blast radius of a bug in this script limited to the working
tree, and lets the workflow apply the repo's usual commit-message and branch
naming conventions in one visible place.

Required environment variables:
    OPENAI_API_KEY   - OpenAI-compatible API key
    OPENAI_BASE_URL  - OpenAI-compatible base URL (chat/completions endpoint root)
    TASK_COMMAND     - the natural-language task text (required)
    CHAT_ID          - Telegram chat id, used only for log context here;
                       the actual Telegram reply is sent by the workflow
    AGENT_MODEL      - optional override, defaults to "gpt-5-codex"
    AGENT_MAX_TURNS  - optional override, defaults to 18

Exit codes:
    0 - agent finished (changes may or may not have been made — check
        `git status --porcelain` after this script returns)
    1 - agent could not complete the task; a precise reason is printed to
        stdout prefixed with "AGENT_FAILURE:" for the workflow to relay
        back to Telegram verbatim (never a generic "something went wrong")
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

_REPO_ROOT = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Directories the agent may never write into or delete from, regardless of
# what the model asks for. Keeps a misbehaving model from nuking git history,
# CI credentials, or its own instructions.
_PROTECTED_PATHS = (".git", ".github/workflows")

# Commands that are never allowed, even if the model tries — these could
# destroy repo state or exfiltrate secrets in ways `run_command`'s working
# directory sandbox doesn't otherwise prevent.
_BLOCKED_COMMAND_SUBSTRINGS = (
    "rm -rf /", "rm -rf .git", "git push", "git commit", "curl ", "wget ",
    ":(){:|:&};:", "> /dev/sda", "mkfs", "shutdown", "reboot",
)

MAX_FILE_READ_BYTES = 60_000
MAX_TOOL_OUTPUT_CHARS = 8_000


class AgentError(Exception):
    """Raised for any failure that should be reported verbatim, not swallowed."""


# ---------------------------------------------------------------------------
# Repo context loading
# ---------------------------------------------------------------------------

def _read_text(path: Path, limit: int = MAX_FILE_READ_BYTES) -> str:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ""
    if len(data) > limit:
        data = data[:limit] + b"\n...[truncated]..."
    return data.decode("utf-8", errors="replace")


def _pick_skill_file(task: str) -> str | None:
    """Very small keyword router mirroring .agent/QUICKSTART.md's table."""
    t = task.lower()
    table = [
        (("fix", "bug", "broken", "error", "not working", "crash"), "fix-bug.md"),
        (("add feature", "implement", "new option", "support for", "add a", "add --"), "add-feature.md"),
        (("new tool", "scaffold", "new cli"), "new-tool-scaffold.md"),
        (("refactor", "clean up", "cleanup", "reorganize", "simplify", "dedup"), "refactor.md"),
        (("dependency", "bump", "upgrade", "requirements.txt", "package version"), "dependency-update.md"),
        (("security", "secret", "audit", "leak", "token exposed"), "security-audit.md"),
        (("action failed", "workflow error", "worker broken", "pipeline fail", "ci error", "dispatch not working"), "ci-cd-recovery.md"),
        (("slow", "performance", "optimi", "speed up", "benchmark"), "performance-check.md"),
        (("revert", "rollback", "undo", "bad merge"), "rollback.md"),
        (("test", "add tests", "write tests"), "test-writing.md"),
        (("docs", "readme", "documentation", "sync"), "docs-sync.md"),
        (("deploy", "pre-deploy", "ready to ship", "production check"), "deploy-check.md"),
    ]
    for keywords, filename in table:
        if any(k in t for k in keywords):
            return filename
    return None


def build_system_prompt(task: str) -> str:
    agents_md = _read_text(_REPO_ROOT / "AGENTS.md")
    memory_md = _read_text(_REPO_ROOT / ".agent" / "MEMORY.md")
    skill_name = _pick_skill_file(task)
    skill_md = ""
    if skill_name:
        skill_path = _REPO_ROOT / ".agent" / "skills" / skill_name
        if skill_path.is_file():
            skill_md = f"\n\n## Matching skill file (.agent/skills/{skill_name})\n\n{_read_text(skill_path)}"

    return f"""You are a headless coding agent operating autonomously on the
"video-pipeline" repository. You were triggered by a Telegram command; there
is no human available to answer follow-up questions, so make reasonable
decisions yourself and proceed.

You have tools to list files, read files, write files (full-content
overwrite), and run shell commands (for checks like syntax validation —
NOT for git commit/push, which the calling workflow handles after you finish).

Ground rules (from AGENTS.md, non-negotiable):
1. Never delete files without being explicitly asked.
2. Never commit or write real secrets — use ${{PLACEHOLDER}} style env var
   names only.
3. Preserve backward compatibility: existing CLI flags, env var names, and
   Telegram bot command handlers must keep working exactly as before unless
   the task explicitly asks to change them.
4. Make the smallest change that correctly accomplishes the task.
5. You may NOT write to `.git/` or `.github/workflows/` (blocked by the tool
   layer — GitHub Actions tokens can't modify workflow files anyway).
6. When you believe the task is complete, call `finish` with a summary and a
   short commit-message-style description (`type(scope): description`
   format, matching this repo's convention).
7. If the task is impossible, out of scope, or you get stuck after
   reasonable effort, call `give_up` with a precise, specific reason —
   never a vague "something went wrong".

# AGENTS.md

{agents_md}

# .agent/MEMORY.md

{memory_md}
{skill_md}

## Task

{task}
"""


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _resolve(rel_path: str) -> Path:
    p = (_REPO_ROOT / rel_path).resolve()
    if _REPO_ROOT not in p.parents and p != _REPO_ROOT:
        raise AgentError(f"Path escapes repo root: {rel_path}")
    rel = p.relative_to(_REPO_ROOT)
    for protected in _PROTECTED_PATHS:
        if str(rel) == protected or str(rel).startswith(protected + os.sep):
            raise AgentError(f"Path is protected and cannot be written by the agent: {rel_path}")
    return p


def tool_list_files(pattern: str = "**/*") -> str:
    matches = []
    for path in _REPO_ROOT.rglob("*"):
        if ".git" in path.parts:
            continue
        rel = str(path.relative_to(_REPO_ROOT))
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(os.path.basename(rel), pattern):
            matches.append(rel + ("/" if path.is_dir() else ""))
        if len(matches) >= 500:
            matches.append("...[truncated at 500 results]...")
            break
    return "\n".join(sorted(matches)) if matches else "(no matches)"


def tool_read_file(path: str) -> str:
    full = _resolve(path)
    if not full.is_file():
        return f"ERROR: not a file: {path}"
    content = _read_text(full)
    numbered = "\n".join(f"{i+1:5d}\t{line}" for i, line in enumerate(content.splitlines()))
    return numbered or "(empty file)"


def tool_write_file(path: str, content: str) -> str:
    full = _resolve(path)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {path}"


def tool_run_command(command: str) -> str:
    lowered = command.lower()
    for blocked in _BLOCKED_COMMAND_SUBSTRINGS:
        if blocked in lowered:
            return f"ERROR: command blocked by agent sandbox policy: contains '{blocked}'"
    try:
        result = subprocess.run(
            command, shell=True, cwd=str(_REPO_ROOT),
            capture_output=True, text=True, timeout=120,
        )
        out = (result.stdout or "") + (result.stderr or "")
        if len(out) > MAX_TOOL_OUTPUT_CHARS:
            out = out[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated]..."
        return f"exit_code={result.returncode}\n{out}"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 120s"
    except Exception as e:
        return f"ERROR: {e}"


TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List repo files matching a glob pattern (default: everything, excluding .git).",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string", "description": "Glob pattern, e.g. 'aspectshift/*.py'"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a repo-relative file's full content, with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Overwrite a repo-relative file with new full content (creates it and parent dirs if needed). Cannot write to .git/ or .github/workflows/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a read-only/verification shell command in the repo root (e.g. syntax checks, --help, tests). git commit/push and network fetches are blocked — the workflow handles those.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Call when the task is complete. Provide a commit-message-style summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Human-readable summary of what changed and why."},
                    "commit_message": {"type": "string", "description": "e.g. 'feat(aspectshift): add --speed flag'"},
                },
                "required": ["summary", "commit_message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "give_up",
            "description": "Call if the task cannot be completed. Provide a precise, specific reason.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]

TOOL_IMPL = {
    "list_files": lambda args: tool_list_files(args.get("pattern", "**/*")),
    "read_file": lambda args: tool_read_file(args["path"]),
    "write_file": lambda args: tool_write_file(args["path"], args.get("content", "")),
    "run_command": lambda args: tool_run_command(args["command"]),
}


# ---------------------------------------------------------------------------
# Post-change verification (mirrors .agent/hooks/pre-commit-checklist.md)
# ---------------------------------------------------------------------------

def run_precommit_checklist() -> list[str]:
    """Returns a list of human-readable problems found (empty = all clear)."""
    problems: list[str] = []

    diff = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=_REPO_ROOT,
                           capture_output=True, text=True)
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=_REPO_ROOT,
                                capture_output=True, text=True)
    changed_files = [f for f in (diff.stdout.splitlines() + untracked.stdout.splitlines()) if f]

    for f in changed_files:
        full = _REPO_ROOT / f
        if not full.is_file():
            continue
        if f.endswith(".py"):
            r = subprocess.run([sys.executable, "-m", "py_compile", str(full)],
                                capture_output=True, text=True)
            if r.returncode != 0:
                problems.append(f"Python syntax error in {f}:\n{r.stderr.strip()}")
        elif f.endswith((".yml", ".yaml")):
            r = subprocess.run(
                [sys.executable, "-c", f"import yaml; yaml.safe_load(open({str(full)!r}))"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                problems.append(f"Invalid YAML in {f}:\n{r.stderr.strip()}")
        elif f == "cloudflare-worker/worker.js" or f.endswith(".js"):
            r = subprocess.run(["node", "-c", str(full)], capture_output=True, text=True)
            if r.returncode != 0:
                problems.append(f"JavaScript syntax error in {f}:\n{r.stderr.strip()}")

    # Secrets scan across the diff, mirroring pre-commit-checklist.md
    r = subprocess.run(["git", "diff", "HEAD"], cwd=_REPO_ROOT, capture_output=True, text=True)
    import re
    suspicious = re.findall(r'(sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{10,}|Bearer [A-Za-z0-9._-]{10,})', r.stdout)
    if suspicious:
        problems.append(f"Possible hardcoded secret literal(s) found in diff: {suspicious[:3]}")

    if any(f.startswith(".github/workflows/") for f in changed_files):
        problems.append(".github/workflows/ was modified — this should be impossible (blocked by write_file); investigate immediately.")

    return problems


# ---------------------------------------------------------------------------
# LLM tool-use loop
# ---------------------------------------------------------------------------

def _get_openai_client():
    try:
        from openai import OpenAI
    except ImportError as e:
        raise AgentError("openai package is not installed (add to requirements.txt / pip install openai)") from e

    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")
    if not api_key or not base_url:
        raise AgentError("OPENAI_API_KEY / OPENAI_BASE_URL not set — cannot run the agent LLM loop.")
    return OpenAI(api_key=api_key, base_url=base_url)


def run_agent(task: str) -> tuple[bool, str]:
    """Returns (success, message). message is either a finish summary or a
    give_up / failure reason suitable for relaying to Telegram verbatim."""

    client = _get_openai_client()
    model = os.environ.get("AGENT_MODEL", "gpt-5-codex")
    max_turns = int(os.environ.get("AGENT_MAX_TURNS", "18"))

    messages = [
        {"role": "system", "content": build_system_prompt(task)},
        {"role": "user", "content": f"Begin. Task: {task}"},
    ]

    for turn in range(1, max_turns + 1):
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, tools=TOOLS_SPEC, tool_choice="auto",
            )
        except Exception as e:
            raise AgentError(f"LLM API call failed on turn {turn}: {e}")

        msg = resp.choices[0].message
        messages.append({"role": "assistant", "content": msg.content or "",
                          "tool_calls": [tc.model_dump() for tc in (msg.tool_calls or [])] or None})

        if not msg.tool_calls:
            # Model spoke without calling a tool — nudge it back on track
            # once, then treat as give_up if it stalls again.
            messages.append({"role": "user", "content":
                              "Please call a tool (list_files/read_file/write_file/run_command) "
                              "or call finish/give_up. Do not respond with plain text only."})
            continue

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "finish":
                summary = args.get("summary", "(no summary provided)")
                commit_message = args.get("commit_message", "agent: automated change")
                problems = run_precommit_checklist()
                if problems:
                    # Feed the problems back so the model can self-correct
                    # instead of the workflow committing broken code.
                    report = "\n\n".join(problems)
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id, "name": name,
                        "content": (f"finish() rejected — pre-commit checklist found problems, "
                                     f"please fix and call finish again:\n\n{report}"),
                    })
                    continue
                return True, f"{commit_message}\n\n{summary}"

            if name == "give_up":
                reason = args.get("reason", "(no reason provided)")
                return False, reason

            impl = TOOL_IMPL.get(name)
            if impl is None:
                output = f"ERROR: unknown tool '{name}'"
            else:
                try:
                    output = impl(args)
                except AgentError as e:
                    output = f"ERROR: {e}"
                except Exception as e:
                    output = f"ERROR: {e}\n{traceback.format_exc()[-1500:]}"

            messages.append({"role": "tool", "tool_call_id": tc.id, "name": name, "content": str(output)[:MAX_TOOL_OUTPUT_CHARS]})

    return False, f"Agent did not finish within {max_turns} turns — task may be too large for one run. Try breaking it into smaller steps."


def main() -> int:
    task = os.environ.get("TASK_COMMAND", "").strip()
    if not task:
        print("AGENT_FAILURE: TASK_COMMAND environment variable is empty.")
        return 1

    chat_id = os.environ.get("CHAT_ID", "")
    print(f"[agent_runner] Task: {task!r} (chat_id={chat_id})")

    try:
        success, message = run_agent(task)
    except AgentError as e:
        print(f"AGENT_FAILURE: {e}")
        return 1
    except Exception as e:
        print(f"AGENT_FAILURE: unexpected error: {e}\n{traceback.format_exc()[-2000:]}")
        return 1

    if success:
        print(f"AGENT_SUCCESS: {message}")
        # Write the commit message for the workflow to pick up without
        # re-parsing stdout.
        (_REPO_ROOT / ".agent_commit_message.tmp").write_text(message, encoding="utf-8")
        return 0

    print(f"AGENT_FAILURE: {message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
