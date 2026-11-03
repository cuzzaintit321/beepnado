#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v git >/dev/null || { echo "git is required"; exit 1; }
command -v gh >/dev/null || { echo "GitHub CLI (gh) is required"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Run gh auth login first"; exit 1; }
owner="$(gh api user --jq '.login')"
if gh repo view "$owner/beepnado" >/dev/null 2>&1; then
  echo "Repository $owner/beepnado already exists. Refusing to overwrite it."
  exit 1
fi
if [ -d .git ]; then
  echo "This folder already has a Git repository. Refusing to reinitialize."
  exit 1
fi
if [ -f .env ]; then
  echo "Local .env detected. It is ignored by Git, but double-check secrets before publishing."
fi
git init -b main
git add --all
git diff --cached --name-only | grep -E '^\.env$|\.sqlite3' && { echo "Unsafe files detected: review index"; exit 1; } || true
git commit -m "Launch BeepNado uptime monitoring SaaS foundation"
gh repo create "$owner/beepnado" --private --source=. --remote=origin --push
echo "Published: https://github.com/$owner/beepnado"
