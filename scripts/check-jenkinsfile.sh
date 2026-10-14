#!/usr/bin/env bash
# =============================================================================
# check-jenkinsfile.sh — Jenkinsfile 漂移门禁（提交门禁 / CI 共用同一断言）
# =============================================================================
# 作用: 由 jenkins-config.yml 重新生成 Jenkinsfile，与仓库内文件比对；
#       不一致 → 打印 漂移位置 + 实际/期望 diff + 修复命令，退出码 1。
#
# 生成器解析顺序（自包含优先，保证 CI 离线可跑）:
#   1. $JENKINSFILE_GENERATOR                    显式指定
#   2. <repo>/tools/jenkins/scripts/generate-jenkinsfile.py   （install-jenkinsfile-gate.sh 固化）
#   3. ~/.agents/skills/jenkins/scripts/generate-jenkinsfile.py （本机技能）
#
# 用法:
#   bash scripts/check-jenkinsfile.sh                    # 默认 jenkins-config.yml → Jenkinsfile
#   bash scripts/check-jenkinsfile.sh -c jenkins-config.yml -o Jenkinsfile
#   JENKINSFILE_GENERATOR=/path/to/generate-jenkinsfile.py bash scripts/check-jenkinsfile.sh
# =============================================================================
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
CONFIG="jenkins-config.yml"
OUTPUT="Jenkinsfile"

while [ $# -gt 0 ]; do
    case "$1" in
        -c|--config) CONFIG="$2"; shift 2 ;;
        -o|--output) OUTPUT="$2"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

# 定位生成器（自包含优先）
GEN="${JENKINSFILE_GENERATOR:-}"
if [ -z "$GEN" ]; then
    if [ -f "${ROOT}/tools/jenkins/scripts/generate-jenkinsfile.py" ]; then
        GEN="${ROOT}/tools/jenkins/scripts/generate-jenkinsfile.py"
    elif [ -f "${HOME}/.agents/skills/jenkins/scripts/generate-jenkinsfile.py" ]; then
        GEN="${HOME}/.agents/skills/jenkins/scripts/generate-jenkinsfile.py"
    fi
fi

if [ -z "$GEN" ] || [ ! -f "$GEN" ]; then
    echo "❌ Jenkinsfile 门禁无法执行: 未找到 generate-jenkinsfile.py" >&2
    echo "   异常分类: GENERATOR_MISSING" >&2
    echo "   修复命令: bash ~/.agents/skills/jenkins/scripts/install-jenkinsfile-gate.sh \"${ROOT}\"" >&2
    echo "   （或设置 JENKINSFILE_GENERATOR=<生成器路径>）" >&2
    exit 1
fi

if [ ! -f "${ROOT}/${CONFIG}" ] && [ ! -f "${CONFIG}" ]; then
    echo "❌ Jenkinsfile 门禁无法执行: 配置文件缺失: ${CONFIG}" >&2
    echo "   异常分类: CONFIG_MISSING" >&2
    echo "   修复命令: 在仓库根创建 ${CONFIG}（脚手架见 ~/.agents/skills/jenkins/templates/jenkins-config-*.yml）" >&2
    exit 1
fi

CFG_PATH="$CONFIG"
[ -f "${ROOT}/${CONFIG}" ] && CFG_PATH="${ROOT}/${CONFIG}"
OUT_PATH="$OUTPUT"
[ "${OUTPUT:0:1}" != "/" ] && OUT_PATH="${ROOT}/${OUTPUT}"

python3 "$GEN" -y "$CFG_PATH" -o "$OUT_PATH" --check
