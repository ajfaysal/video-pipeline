# Agent Infrastructure — Quick Start

> Last updated: 2026-07-11

## What Was Set Up

| File/Folder | Purpose |
|-------------|---------|
| `AGENTS.md` | Master instructions for any AI agent — project overview, tech stack, folder structure, coding standards, rules, trigger architecture |
| `.agent/skills/fix-bug.md` | Step-by-step procedure for reproducing, isolating, patching, and committing bug fixes |
| `.agent/skills/add-feature.md` | Plan → implement → test → document → commit workflow for new features |
| `.agent/skills/refactor.md` | Safe refactoring procedure with behavior-preservation guarantees |
| `.agent/skills/deploy-check.md` | Pre-deploy checklist: lint, build, env vars, secrets, backward compat |
| `.agent/skills/debug-pipeline.md` | Diagnose GitHub Actions / Cloudflare Worker failures: read logs, isolate, fix |
| `.agent/skills/add-telegram-command.md` | Safely add a new bot command without touching any existing command handler |
| `.agent/skills/new-tool-scaffold.md` | Scaffold a brand-new CLI tool following this repo's existing tool structure |
| `.agent/skills/dependency-update.md` | Safely bump a `requirements.txt` package, check for breaking changes, test first |
| `.agent/skills/security-audit.md` | Scan for hardcoded secrets, overly broad permissions, exposed tokens before every PR |
| `.agent/skills/ci-cd-recovery.md` | Diagnose + self-heal (bounded) GitHub Action / Cloudflare Worker deploy failures |
| `.agent/skills/performance-check.md` | Flag slow video-processing steps, suggest concrete optimizations, benchmark before/after |
| `.agent/skills/rollback.md` | Safely revert a bad merge — revert PR only, never force-push main, never delete branches without confirmation |
| `.agent/skills/test-writing.md` | Add minimal but meaningful tests for new features before marking a task done |
| `.agent/skills/docs-sync.md` | Keep README.md and AGENTS.md in sync whenever a tool's CLI flags change |
| `.agent/MEMORY.md` | Running log of architectural decisions, library choices, known issues |
| `.agent/hooks/pre-commit-checklist.md` | What to verify before every commit |
| `.agent/hooks/post-edit-checklist.md` | What to verify after any file edit |
| `bot/agent_runner.py` | Legacy internal runner kept for reference; current headless path uses Grok CLI in `docs/agent-task.yml` |
| `docs/agent-task.yml` | Headless agent trigger workflow (copy to `.github/workflows/` — see manual step below) |
| `.mcp/config.json` | MCP server config template (GitHub, filesystem, memory) with placeholder env vars |

## How Tasks Get Triggered (end-to-end, now live)

```
Telegram: /agent add a --speed flag to aspectshift
  │
  ▼
cloudflare-worker/worker.js
  handleAgentCommand() — checks chat_id against env.AGENT_CHAT_ID (fails
  closed if unset), then dispatchAgentTask() fires:
    POST /repos/{owner}/{repo}/dispatches
    { "event_type": "agent_command",
      "client_payload": { "command": "...", "chat_id": "..." } }
  Immediately replies: "🚀 Started: <task>"
  │
  ▼
.github/workflows/agent-task.yml (once manually installed — see below)
  1. Checkout, create branch agent/<slug>-<run-id>
  2. Install Grok CLI and restore `~/.grok/auth.json` from `GROK_AUTH_JSON`
  3. Run `grok agent run --headless --yes --prompt "$COMMAND"`
  4. Commit, push the branch
  5. gh pr create --base main --head agent/<slug>-<run-id>
  6. Telegram: "✅ Done — tap to review & merge: <PR URL>"
  Any failure at any step -> Telegram gets the *specific* error, not a
  generic failure message (see the "Notify Telegram - X failed" steps).
```

### From GitHub CLI (manual trigger, bypassing Telegram)
```bash
gh api repos/OWNER/REPO/dispatches \
  -f event_type=agent_command \
  -f client_payload='{"command":"add --speed flag to aspectshift","chat_id":"123456"}'
```

### From curl (manual trigger)
```bash
curl -X POST https://api.github.com/repos/OWNER/REPO/dispatches \
  -H "Authorization: token YOUR_GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  -d '{"event_type":"agent_command","client_payload":{"command":"refactor shared download logic","chat_id":"123456"}}'
```

## Which Skill File for Common Telegram `/agent` Phrasings

| Telegram command phrasing (examples) | Skill File |
|---------------------------------------|------------|
| "fix bug", "debug", "broken", "not working", "crash" | `.agent/skills/fix-bug.md` |
| "add feature", "implement", "new option", "support for" | `.agent/skills/add-feature.md` |
| "add a command", "new bot command", "add /something" | `.agent/skills/add-telegram-command.md` |
| "new tool", "scaffold a tool", "new CLI" | `.agent/skills/new-tool-scaffold.md` |
| "refactor", "clean up", "reorganize", "simplify", "dedupe" | `.agent/skills/refactor.md` |
| "bump", "upgrade", "update dependency", "requirements.txt" | `.agent/skills/dependency-update.md` |
| "security audit", "scan for secrets", "exposed token" | `.agent/skills/security-audit.md` |
| "deploy check", "ready to ship", "production check" | `.agent/skills/deploy-check.md` |
| "action failed", "workflow error", "worker broken", "self-heal" | `.agent/skills/ci-cd-recovery.md` / `.agent/skills/debug-pipeline.md` |
| "slow", "performance", "optimize", "benchmark" | `.agent/skills/performance-check.md` |
| "revert", "rollback", "undo", "bad merge" | `.agent/skills/rollback.md` |
| "add tests", "write tests", "test this" | `.agent/skills/test-writing.md` |
| "update docs", "sync README", "CLI flags changed" | `.agent/skills/docs-sync.md` |

`bot/agent_runner.py`'s `_pick_skill_file()` implements this exact routing
automatically — this table documents that logic for humans and for any
agent session reasoning about which skill applies before diving in.

## Manual Steps Required (one-time, exact)

GitHub Apps cannot push `.github/workflows/*` changes without the
`workflows` permission. A human must do these exact steps once.

### 1) Copy workflow draft into the real workflows folder
```bash
git checkout main && git pull --ff-only && cp docs/agent-task.yml .github/workflows/agent-task.yml && git add .github/workflows/agent-task.yml && git commit -m "ci: add agent-task workflow" && git push
```

### 2) Create `GROK_AUTH_JSON` secret from real Grok CLI browser login
Run this in Codespaces (or any shell with browser login support):
```bash
curl -fsSL https://x.ai/cli/install.sh | bash && grok auth login && cat ~/.grok/auth.json
```
Then in GitHub mobile/web:
1. Open repo → **Settings** → **Secrets and variables** → **Actions**
2. Tap **New repository secret**
3. Name: `GROK_AUTH_JSON`
4. Value: paste the full JSON printed by `cat ~/.grok/auth.json`
5. Tap **Add secret**

### 3) Ensure Telegram token secret exists
In the same Actions secrets screen, ensure `TELEGRAM_BOT_TOKEN` exists.
If missing, create it with the bot token from @BotFather.

### 4) Set Worker allowlist chat ID and redeploy Worker
```bash
cd cloudflare-worker && npx wrangler secret put AGENT_CHAT_ID && npx wrangler deploy
```
When prompted by `wrangler secret put`, paste your Telegram numeric `chat_id`
(e.g. from @userinfobot).

> Headless model invocation is: `grok agent run --headless --yes --prompt "$COMMAND"`.
> No paid API key is required.

## Agent Session Protocol

1. Read `AGENTS.md` (conventions + rules)
2. Read `.agent/MEMORY.md` (past decisions + known issues)
3. Check `.agent/skills/` for a matching procedure (see routing table above)
4. Do the work
5. Run `.agent/hooks/pre-commit-checklist.md` checks
6. Commit with `type: description` format
7. Append to `.agent/MEMORY.md` if you made a significant decision
