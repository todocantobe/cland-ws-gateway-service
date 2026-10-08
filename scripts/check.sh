#!/usr/bin/env bash
# ==============================================================================
# 脚本名称: check.sh  （模板统一门禁入口 · 唯一入口，本地与 Jenkins 共用）
# 规范: cland-pm/docs/门禁规范.md（v0.1）· #397
# 说明: 本文件**在所有模板中保持字节级同构**——语言差异只落在两份可替换文件：
#         - gate.yaml         各语言门禁项声明（顺序 = 规范顺序）
#         - scripts/gate-pre.sh  可选：语言前置步骤（环境同步等）；缺失则跳过
# 检查器 runner: tools/gate.py（commit-gate 技能资产，零依赖）
# 用法:
#   ./scripts/check.sh                    # 全量（= CI/Jenkins 调用）
#   ./scripts/check.sh --stage fast --changed   # 本地 pre-commit 子集
#   ./scripts/check.sh --fix              # 先格式化再检查
#   ./scripts/check.sh --exposure         # 追加接口暴露面扫描（发布前）
# 退出码: 0=全绿; 1=有门禁失败; 2=用法错误
# ==============================================================================
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly ROOT_DIR="$(dirname "${SCRIPT_DIR}")"
cd "${ROOT_DIR}"

STAGE="all"
CHANGED=0
FIX=0
EXPOSURE=0
readonly ORIG_ARGS=("$@")

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stage) STAGE="${2:?--stage 需要 all|fast|slow}"; shift 2 ;;
        --changed) CHANGED=1; shift ;;
        --fix) FIX=1; shift ;;
        --exposure) EXPOSURE=1; shift ;;
        -h|--help)
            sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "未知参数: $1（支持 --stage/--changed/--fix/--exposure）" >&2; exit 2 ;;
    esac
done

log()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$1"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# --- 前置（语言无关；语言差异在 scripts/gate-pre.sh） --------------------------
if [[ -f "${SCRIPT_DIR}/gate-pre.sh" ]]; then
    log "前置步骤 (scripts/gate-pre.sh)"
    # shellcheck source=/dev/null
    bash "${SCRIPT_DIR}/gate-pre.sh" "${ORIG_ARGS[@]}"
fi

# --- 可选：自动修复（仅本地；CI 不用） ---------------------------------------
if [[ "${FIX}" -eq 1 ]]; then
    log "自动修复（--fix）"
    if [[ -f "${SCRIPT_DIR}/gate-fix.sh" ]]; then
        bash "${SCRIPT_DIR}/gate-fix.sh"
    else
        echo "  （无 scripts/gate-fix.sh，跳过）"
    fi
fi

# --- 统一门禁 runner（gate.yaml 声明 9 项，顺序=规范顺序） ---------------------
[[ -f "tools/gate.py" ]] || fail "缺少 tools/gate.py（commit-gate 资产未安装）"
ARGS=(--stage "${STAGE}")
[[ "${CHANGED}" -eq 1 ]] && ARGS+=(--changed)
log "门禁 runner: tools/gate.py ${ARGS[*]}"
python3 tools/gate.py "${ARGS[@]}"

# --- 可选：接口暴露面（登记项，默认不启用；发布前） ---------------------------
if [[ "${EXPOSURE}" -eq 1 ]]; then
    log "接口暴露面扫描"
    [[ -f "${SCRIPT_DIR}/scan-exposure.sh" ]] \
        && bash "${SCRIPT_DIR}/scan-exposure.sh" --fail-on-high . \
        || fail "指定 --exposure 但缺少 scripts/scan-exposure.sh"
fi

printf '\n\033[1;32m✅ 全部门禁通过（%s）\033[0m\n' "${STAGE}"
