#!/usr/bin/env bash
# ==============================================================================
# deploy-gate.sh — Jenkins 部署前置门禁 G0–G3（#398）
# ==============================================================================
# 在 Jenkins「部署阶段」前执行；任一不达标 → exit 1（打印原因），部署不放行。
#
#   G0 部署前置 : 迁移就绪（scripts/check-migration-ready.sh）+ 环境 + 回滚点
#   G1 分支门禁 : release/* ⊇ develop（develop ⊆ HEAD）；hotfix/* ⊇ master
#   G2 一致性   : 存在 Jenkinsfile 即强制校验 = 生成件（scripts/check-jenkinsfile.sh 重生成比对）。
#   G3 放行门禁 : RUN_DEPLOY == true（显式）+ 分支匹配
#
# 用法:
#   deploy-gate.sh --env prod --branch release/20261008 --run-deploy true
#   （未显式传 RUN_DEPLOY=true → 直接拦截）
# 退出码: 0=放行; 1=拦截; 2=用法错误
# ==============================================================================
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
ENV="dev"; BRANCH=""; RUN_DEPLOY_REQ=""
ROLLBACK_POINT="${ROLLBACK_POINT:-}"
REQUIRE_MIGRATION="auto"   # auto|1|0
REQUIRE_ROLLBACK="auto"

while [ $# -gt 0 ]; do
    case "$1" in
        --env) ENV="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --run-deploy) RUN_DEPLOY_REQ="$2"; shift 2 ;;
        --rollback-point) ROLLBACK_POINT="$2"; shift 2 ;;
        --require-migration) REQUIRE_MIGRATION="$2"; shift 2 ;;
        --require-rollback) REQUIRE_ROLLBACK="$2"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

[ -n "$BRANCH" ] || BRANCH="${BRANCH_NAME:-${GIT_BRANCH:-}}"
[ -n "$BRANCH" ] || BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
BRANCH="${BRANCH#origin/}"
[ -n "$RUN_DEPLOY_REQ" ] || RUN_DEPLOY_REQ="${RUN_DEPLOY:-}"

# prod 默认要求迁移前置；dev/test 未提供则跳过
if [ "$REQUIRE_MIGRATION" = "auto" ]; then
    [ "$ENV" = "prod" ] && REQUIRE_MIGRATION=1 || REQUIRE_MIGRATION=0
fi
[ "$REQUIRE_ROLLBACK" = "auto" ] && REQUIRE_ROLLBACK=0

FAIL=0
gate_fail() { printf '\033[31m❌ [%s] %s\033[0m\n' "$1" "$2" >&2; FAIL=1; }
gate_pass() { printf '\033[32m✅ [%s] %s\033[0m\n' "$1" "$2"; }
gate_skip() { printf '\033[33m⏭  [%s] %s\033[0m\n' "$1" "$2"; }

echo "=== Jenkins 部署门禁 G0–G3 (env=${ENV}, branch=${BRANCH}) ==="

# ── G3 放行门禁：RUN_DEPLOY 显式 ──────────────────────────────────────────────
if [ "$(printf '%s' "$RUN_DEPLOY_REQ" | tr '[:upper:]' '[:lower:]')" = "true" ]; then
    gate_pass G3 "RUN_DEPLOY=true（显式请求部署）"
else
    gate_fail G3 "未显式请求部署：RUN_DEPLOY='${RUN_DEPLOY_REQ}'（必须显式传 true；默认 false 不部署）"
fi

# ── G1 分支门禁 ──────────────────────────────────────────────────────────────
require_ancestor() { # require_ancestor <label> <candidate refs...>
    local label="$1"; shift
    local ref="" c
    for c in "$@"; do
        if git rev-parse --verify -q "$c" >/dev/null 2>&1; then ref="$c"; break; fi
    done
    if [ -z "$ref" ]; then
        # 尝试 fetch 远程（Jenkins 多分支检出后通常已存在 origin/*）
        for c in "$@"; do
            git fetch -q origin "${c#origin/}" >/dev/null 2>&1 || true
            git rev-parse --verify -q "$c" >/dev/null 2>&1 && { ref="$c"; break; }
        done
    fi
    if [ -z "$ref" ]; then
        gate_fail G1 "无法解析 $label（候选：$*）"
        return
    fi
    if git merge-base --is-ancestor "$ref" HEAD; then
        gate_pass G1 "$label ⊆ HEAD（${ref} 是 HEAD 祖先）"
    else
        gate_fail G1 "分支不合规：${label}（${ref}）不是 HEAD 的祖先（$label ⊄ HEAD，须从 ${label} 切/回合）"
    fi
}
case "$BRANCH" in
    release/*) require_ancestor "develop" "origin/develop" "develop" ;;
    hotfix/*)  require_ancestor "master" "origin/master" "master" "origin/main" "main" ;;
    master|main) gate_pass G1 "分支 ${BRANCH}（回合分支，跳过祖先断言）" ;;
    *) gate_fail G1 "不可部署分支：${BRANCH}（仅 release/* · hotfix/* · master/main）" ;;
esac

# ── G2 Jenkinsfile 一致性（#388：生成件比对）─────────────────────────────────
# 规则：**只要存在 Jenkinsfile 就必须校验**（统一模板，禁止自定义/私改）。
if [ -f "${ROOT}/Jenkinsfile" ]; then
    if [ ! -f "${ROOT}/scripts/check-jenkinsfile.sh" ]; then
        gate_fail G2 "存在 Jenkinsfile 但未装生成件一致性门禁（scripts/check-jenkinsfile.sh）；统一模板强制校验"
    elif [ ! -f "${ROOT}/jenkins-config.yml" ]; then
        gate_fail G2 "存在 Jenkinsfile 但无 jenkins-config.yml（非生成器托管，禁止自定义）"
    elif bash "${ROOT}/scripts/check-jenkinsfile.sh" >/tmp/jenkinsfile-gate.out 2>&1; then
        gate_pass G2 "Jenkinsfile 与配置重生成一致（#388）"
    else
        gate_fail G2 "Jenkinsfile 漂移（#388）：$(sed -n '1,3p' /tmp/jenkinsfile-gate.out | tr '\n' ' ')"
    fi
else
    gate_skip G2 "无 Jenkinsfile，跳过生成件一致性"
fi

# ── G0 部署前置 ──────────────────────────────────────────────────────────────
# G0a 环境
case "$ENV" in
    dev|test|prod) gate_pass G0 "DEPLOY_ENV=${ENV}" ;;
    *) gate_fail G0 "非法 DEPLOY_ENV=${ENV}（须 dev|test|prod）" ;;
esac
# G0b 迁移就绪（I-D1）
if [ -f "${ROOT}/scripts/check-migration-ready.sh" ]; then
    if bash "${ROOT}/scripts/check-migration-ready.sh" >/tmp/migration-ready.out 2>&1; then
        gate_pass G0 "迁移前置就绪（check-migration-ready.sh）"
    else
        gate_fail G0 "迁移未就绪：$(sed -n '1,3p' /tmp/migration-ready.out | tr '\n' ' ')"
    fi
elif [ "$REQUIRE_MIGRATION" = "1" ]; then
    gate_fail G0 "缺迁移前置脚本 scripts/check-migration-ready.sh（${ENV} 要求迁移就绪，I-D1）"
else
    gate_skip G0 "无 check-migration-ready.sh（${ENV} 不强制迁移前置）"
fi
# G0c 权限基线（若提供脚本；#234）
if [ -f "${ROOT}/scripts/privilege-audit.sh" ]; then
    if bash "${ROOT}/scripts/privilege-audit.sh" >/tmp/priv-audit.out 2>&1; then
        gate_pass G0 "权限基线 clean（privilege-audit.sh）"
    else
        gate_fail G0 "权限基线异常：$(sed -n '1,3p' /tmp/priv-audit.out | tr '\n' ' ')"
    fi
else
    gate_skip G0 "无 scripts/privilege-audit.sh（权限基线未接）"
fi
# G0d 配置就绪（若提供脚本）
if [ -f "${ROOT}/scripts/check-deploy-config.sh" ]; then
    if bash "${ROOT}/scripts/check-deploy-config.sh" >/tmp/deploy-config.out 2>&1; then
        gate_pass G0 "部署配置就绪（check-deploy-config.sh）"
    else
        gate_fail G0 "部署配置异常：$(sed -n '1,3p' /tmp/deploy-config.out | tr '\n' ' ')"
    fi
else
    gate_skip G0 "无 scripts/check-deploy-config.sh（配置就绪未接）"
fi
# G0e 回滚点
if [ -n "${ROLLBACK_POINT:-}" ]; then
    gate_pass G0 "回滚点=${ROLLBACK_POINT}"
elif [ "$REQUIRE_ROLLBACK" = "1" ]; then
    gate_fail G0 "未提供回滚点（ROLLBACK_POINT 为空）"
else
    gate_skip G0 "回滚点=未提供（非强制；部署阶段应记录上一镜像 tag）"
fi

echo "================================================"
if [ "$FAIL" -eq 0 ]; then
    echo "✅ 部署门禁 G0–G3 全部通过 → 放行"
    exit 0
fi
echo "❌ 部署门禁存在未通过项 → 拒绝部署" >&2
exit 1
