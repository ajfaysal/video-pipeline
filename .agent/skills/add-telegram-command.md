# Skill: Add Telegram Command

> **Trigger**: "add a command", "new bot command", "add /something", "telegram command"

## Procedure

### 1. Study the existing pattern first
Read `cloudflare-worker/worker.js` end to end before touching it. Note:
- `handleMessage(env, message)` is the single entry point for text messages —
  every command is a branch inside it, checked in order, each returning early.
- `handleCallback(env, callbackQuery)` handles inline-button taps (`kind:value`
  callback_data), separate from text commands.
- Auth pattern: commands that must be restricted to one operator check
  `String(chatId) !== String(env.SOME_CHAT_ID_SECRET)` and fail closed if the
  secret is unset (see `handleAgentCommand` / `AGENT_CHAT_ID` for the
  reference implementation).
- Dispatch pattern: anything that needs GitHub Actions to do real work calls
  `dispatchJob()` (video tools, event_type `telegram-job`) or a sibling
  dispatch function using its own distinct `event_type` (see
  `dispatchAgentTask()`, event_type `agent_command`). **Never reuse an
  existing event_type for a new kind of payload** — the workflow that
  listens for it won't understand the new fields.

### 2. Add the command as a new, self-contained branch
- Put the regex/text check for your new command **first** in `handleMessage`,
  before the `/start|/help|/menu` check, and `return` immediately after
  handling it. This guarantees it can never be shadowed by (or shadow) any
  existing conversation-state (`state.step === ...`) branch below it.
- Write a dedicated `handleXCommand()` function rather than inlining logic —
  keeps `handleMessage` scannable and makes the new command easy to unit-test
  in isolation later.
- If the command needs multi-step input (like LofiLoop's 3-step flow), use
  `setState`/`getState`/`clearState` exactly as the existing flows do — don't
  invent a second state mechanism.

### 3. Never touch existing branches
- Do not reorder, rename, or refactor any existing `if (state && state.step === ...)`
  block, `TOOL_LABELS`, `TOOL_GROUPS`, or `handleCallback` branch while adding
  your command. If you think an existing block needs refactoring, that's a
  separate task — flag it, don't fold it in here.
- Do not change `dispatchJob()`'s signature or `TOOL_RUNNERS` in
  `bot/run_job.py` unless the task is specifically about a video tool.

### 4. Wire any backend processing
- If the command needs GitHub Actions to run code: add a new
  `repository_dispatch` `types:` entry to a workflow (new file preferred; if
  editing `.github/workflows/*.yml` directly is blocked by the GitHub App's
  missing `workflows` permission, put the ready file in `docs/` instead and
  add a manual-install note to `.agent/QUICKSTART.md`, exactly as done for
  `agent-task.yml`).
- If it just needs a synchronous reply (no heavy compute), the Worker can
  answer directly with `sendMessage()` — no dispatch needed.

### 5. Verify
```bash
node -c cloudflare-worker/worker.js   # syntax
grep -c "async function handle" cloudflare-worker/worker.js  # sanity: count unchanged
```
Manually trace through: does every existing `if (text === "/start" ...)`,
every `state.step` branch, and every `handleCallback` `kind` still exist,
unmodified, after your diff? `git diff cloudflare-worker/worker.js` should
show only **additions**, no deleted/modified lines outside your new blocks.

### 6. Commit
`feat(worker): add /<command> command`

## Example

**Input**: "Add a /status command that replies with the bot's uptime and which tools are enabled"

**Output**:
```
Files changed:
  cloudflare-worker/worker.js — added handleStatusCommand(), new first-checked
    branch `if (text === "/status")` in handleMessage(), returns immediately.
    No existing branch touched.

Commit: feat(worker): add /status command reporting enabled tools
```
