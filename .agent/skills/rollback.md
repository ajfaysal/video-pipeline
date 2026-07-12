# Skill: Rollback

> **Trigger**: "revert", "rollback", "undo", "bad merge", "that broke things, undo it"

## Mandatory rules (non-negotiable, from AGENTS.md + repo safety policy)

1. **Never force-push to `main`.** Not even to "clean up" a bad merge.
2. **Never delete a branch without explicit confirmation** in the task text.
   If the task just says "rollback X", that means create a revert — it does
   NOT authorize deleting the branch that introduced X.
3. **Always roll forward via a new PR**, never rewrite history on a shared
   branch. `main` history must stay append-only from the agent's perspective.

## Procedure

### 1. Identify exactly what to revert
```bash
git log --oneline main -20
gh pr list --state merged --limit 10
```
Find the exact merge commit or PR number the task refers to. If ambiguous
(task says "the last change" but multiple PRs merged recently), pick the
most recent merge to `main` and state that assumption explicitly in the
revert PR description — don't guess silently.

### 2. Create the revert as a normal commit on a new branch
```bash
git fetch origin main
git checkout -b agent/revert-<short-desc>-<run-id> origin/main
git revert -m 1 <merge-commit-sha>       # -m 1 required for a merge commit revert
# or, for a single non-merge commit:
git revert <commit-sha>
```
If `git revert` produces conflicts, resolve them by restoring the
pre-change behavior (the whole point of a revert), not by reinterpreting
what the revert "should" do. If conflicts are too complex to resolve
confidently, stop and report exactly which files conflict — do not force
a resolution that might reintroduce the bug differently.

### 3. Never touch the original branch
- Do not delete the branch that was merged.
- Do not amend or rebase the original merge commit.
- The revert is 100% new commits on a 100% new branch.

### 4. Push and open a PR (never push directly to main)
```bash
git push -u origin agent/revert-<short-desc>-<run-id>
gh pr create --base main --head agent/revert-<short-desc>-<run-id> \
  --title "revert: <what> (reverts #<original PR number>)" \
  --body "Reverts #<PR-number> because <reason from task>. See original PR for context."
```

### 5. Verify the revert actually restores prior behavior
```bash
# Run the same checks that would have caught the original bug:
python -c "import bot.run_job"
node -c cloudflare-worker/worker.js
for tool in aspectshift clipharvest watermarkwipe abroll introoutro stitcher audioduck loudnorm autochapters lofiloop; do
  python -c "import ${tool}.main" && echo "OK: $tool" || echo "FAIL: $tool"
done
```

### 6. Commit message
`revert: <short description> (reverts #<PR>)` — always reference the
original PR number for traceability.

## When NOT to use a plain revert

If only *part* of a merged change is bad (e.g. one tool's new flag works
but its README section has a typo that broke rendering), don't revert the
whole PR — make a targeted fix instead and use `.agent/skills/fix-bug.md`.
Reverting is for "this entire change needs to not exist right now."

## Example

**Input**: "The LoudNorm PR broke the menu, roll it back"

**Output**:
```
Identified: PR #12 "feat(loudnorm): add LoudNorm tool" merged as commit a1b2c3d.
Verified this is the most recent merge touching cloudflare-worker/worker.js.

Created branch agent/revert-loudnorm-58204, ran:
  git revert -m 1 a1b2c3d
No conflicts. Verified node -c worker.js passes and menuKeyboard() no longer
references loudnorm.

Opened PR #13: revert: add LoudNorm tool (reverts #12)
Did NOT delete the original feature/loudnorm branch — left intact per policy.

Commit: revert: add LoudNorm tool (reverts #12)
```
