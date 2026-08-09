#!/usr/bin/env bash
# 安装 git 钩子到本仓库 .git/hooks/
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cp "$PROJECT_DIR/scripts/pre-commit" "$PROJECT_DIR/.git/hooks/pre-commit"
chmod +x "$PROJECT_DIR/.git/hooks/pre-commit"
echo "pre-commit 钩子已安装: $PROJECT_DIR/.git/hooks/pre-commit"
