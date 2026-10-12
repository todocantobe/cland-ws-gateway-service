#!/usr/bin/env bash
# 安装本地 git hooks (doclint 门禁)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_SRC="${ROOT}/scripts/git-hooks/pre-commit"
HOOK_DST="${ROOT}/.git/hooks/pre-commit"

mkdir -p "${ROOT}/.git/hooks"
cp "${HOOK_SRC}" "${HOOK_DST}"
chmod +x "${HOOK_DST}"
echo "installed: ${HOOK_DST}"
echo "verified:  $(python3 "${ROOT}/tools/doclint.py" "${ROOT}" --quiet && echo 'doclint OK')"
