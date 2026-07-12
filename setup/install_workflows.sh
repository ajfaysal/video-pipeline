#!/usr/bin/env bash
# One-shot installer for the two new GitHub Actions workflows.
#
# WHY THIS EXISTS: the Genspark AI GitHub App is not granted the `workflows`
# permission, so it cannot push files under .github/workflows/ itself.
# Run this once from a clone of the repo (or the GitHub web editor works too:
# just copy each file from setup/workflows/ into .github/workflows/).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
cp setup/workflows/bot-poller.yml    .github/workflows/bot-poller.yml
cp setup/workflows/deploy-worker.yml .github/workflows/deploy-worker.yml
git add .github/workflows/bot-poller.yml .github/workflows/deploy-worker.yml
git commit -m "chore: install bot-poller and deploy-worker workflows"
git push
echo "✅ Workflows installed. Now: Actions tab -> 'Telegram Menu Bot (Python poller)' -> Run workflow."
