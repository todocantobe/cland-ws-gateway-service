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
#   G0f 目标门禁 : #923 判「目标是不是生产」（opt-in：--deploy-target-verify）——
#                 目标命中 /opt/www（或其子路径）或 www.creating.net.cn 且 APPROVE_PROD≠true ⇒ 失败；
#                 非 prod 的 env 指向生产目标同样失败（DEPLOY_ENV=staging 不是绕过理由）。
#                 判定委托 scripts/verify-deploy-target.sh（自测含 E2 负控 / E3 正控）。
#
# 用法:
#   deploy-gate.sh --env prod --branch release/20261008 --run-deploy true \
#                  --deploy-base /opt/www --deploy-domain www.creating.net.cn --approve-prod true
#   （--deploy-base/--deploy-domain/--approve-prod 缺省取同名环境变量；
#     --deploy-target-verify = 启用 #923 目标门禁（须装 scripts/verify-deploy-target.sh，缺则 fail-closed）；
#     未显式传 RUN_DEPLOY=true → 直接拦截）
# 退出码: 0=放行; 1=拦截; 2=用法错误
# ==============================================================================
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
ENV="dev"; BRANCH=""; RUN_DEPLOY_REQ=""
TARGET_BASE="${DEPLOY_BASE:-}"; TARGET_DOMAIN="${DEPLOY_DOMAIN:-}"; APPROVE_PROD="${APPROVE_PROD:-false}"
DEPLOY_TARGET_VERIFY=0   # #923：opt-in（舰队其它仓不传 ⇒ 行为与旧版一致）
ROLLBACK_POINT="${ROLLBACK_POINT:-}"
REQUIRE_MIGRATION="auto"   # auto|1|0
REQUIRE_ROLLBACK="auto"

while [ $# -gt 0 ]; do
    case "$1" in
        --env) ENV="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --run-deploy) RUN_DEPLOY_REQ="$2"; shift 2 ;;
        --deploy-base) TARGET_BASE="$2"; shift 2 ;;
        --deploy-domain) TARGET_DOMAIN="$2"; shift 2 ;;
        --approve-prod) APPROVE_PROD="$2"; shift 2 ;;
        --deploy-target-verify) DEPLOY_TARGET_VERIFY=1; shift ;;
        --rollback-point) ROLLBACK_POINT="$2"; shift 2 ;;
        --require-migration) REQUIRE_MIGRATION="$2"; shift 2 ;;
        --require-rollback) REQUIRE_ROLLBACK="$2"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

# ── 分支名解析（#330 B1）─────────────────────────────────────────────────────
# 单分支 pipeline（BranchSpec: :origin/master）下 `env.BRANCH_NAME` 为空，
# 而 `git rev-parse --abbrev-ref HEAD` 在 **detached 检出** 返回字面量 "HEAD"
# ⇒ 旧逻辑把它当分支名 ⇒ G1 判「不可部署分支：HEAD」⇒ **任何单分支 job 的 prod 部署都死于此处**。
# 解析顺序：显式 --branch → BRANCH_NAME/GIT_BRANCH → symbolic-ref → 指向 HEAD 的 ref（本地优先，再 origin/*）。
# 解析结果**必须回显**（BRANCH_NAME=<值>（来源=<src>）），否则"解析对了"与"解析错了"在日志里长得一样。
BRANCH_SRC="arg"
if [ -z "$BRANCH" ]; then
    BRANCH="${BRANCH_NAME:-${GIT_BRANCH:-}}"; BRANCH_SRC="env"
fi
BRANCH="${BRANCH#origin/}"
if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then
    BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"; BRANCH_SRC="git-symbolic"
fi
if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then
    # detached：找指向 HEAD 的 ref。refs/heads 先于 refs/remotes/origin（本地名优先），剔除符号 ref HEAD。
    BRANCH="$(git for-each-ref --points-at HEAD --format='%(refname:short)' refs/heads refs/remotes/origin 2>/dev/null \
        | sed 's#^origin/##' | grep -vx 'HEAD' | head -1)"; BRANCH_SRC="git-refs"
fi
BRANCH="${BRANCH#origin/}"
echo "BRANCH_NAME=${BRANCH:-<empty>}（来源=${BRANCH_SRC}）"
[ -n "$RUN_DEPLOY_REQ" ] || RUN_DEPLOY_REQ="${RUN_DEPLOY:-}"

# prod 默认要求迁移前置；dev/test 未提供则跳过
case "$(printf '%s' "$REQUIRE_MIGRATION" | tr '[:upper:]' '[:lower:]')" in
    true|yes|1) REQUIRE_MIGRATION=1 ;;
    false|no|0) REQUIRE_MIGRATION=0 ;;
    *) REQUIRE_MIGRATION=auto ;;
esac
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
    ""|HEAD)
        gate_fail G1 "无法解析分支名（BRANCH_NAME='${BRANCH}'，来源=${BRANCH_SRC}）：detached 检出且无 ref 指向 HEAD。单分支 job 请在 BranchSpec 写默认分支，或显式传 --branch/BUILD_BRANCH"
        ;;
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
    dev|test|staging|prod) gate_pass G0 "DEPLOY_ENV=${ENV}" ;;
    *) gate_fail G0 "非法 DEPLOY_ENV=${ENV}（须 dev|test|staging|prod）" ;;
esac
# G0b 迁移就绪（I-D1）
if [ -f "${ROOT}/scripts/check-migration-ready.sh" ]; then
    if bash "${ROOT}/scripts/check-migration-ready.sh" >/tmp/migration-ready.out 2>&1; then
        gate_pass G0 "迁移前置就绪（check-migration-ready.sh）"
    else
        gate_fail G0 "迁移未就绪：$(sed -n '1,3p' /tmp/migration-ready.out | tr '\n' ' ')"
    fi
elif [ "$REQUIRE_MIGRATION" = "1" ]; then
    # 缺脚本时必须判定「有没有迁移面」，否则纯静态仓（SPA/前端）在 prod 永远无法通过（#330 B2）。
    # 判据只看「迁移面证据」，不猜：有 migrations 目录或 *.sql ⇒ 必须有脚本（fail-closed）；
    # 两者都无 ⇒ 打印依据后放行（这不是"假设没问题"，是"没有可迁移的东西"）。
    mig_dir=""; for d in migrations migration db/migrate sql; do [ -d "${ROOT}/${d}" ] && mig_dir="${d}"; break; done
    mig_sql="$(find "${ROOT}" -maxdepth 3 -name '*.sql' -not -path '*/node_modules/*' -not -path '*/.git/*' 2>/dev/null | head -5)"
    if [ -z "$mig_dir" ] && [ -z "$mig_sql" ]; then
        gate_pass G0 "无迁移面 ⇒ 无需迁移前置（依据：未发现 migrations/migration/db-migrate/sql 目录，且 3 层内无 *.sql；${ENV} 不再要求 check-migration-ready.sh）"
    else
        gate_fail G0 "有迁移面但缺 scripts/check-migration-ready.sh（依据：migrations 目录=${mig_dir:-无}；*.sql 示例=$(printf '%s' "$mig_sql" | head -1)）"
    fi
else
    gate_skip G0 "迁移前置按配置跳过（--require-migration 0 / jenkins-config.yml require_migration: false；${ENV}）"
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
# G0f 目标门禁（#923）：判目标是否生产，不判参数名叫什么（opt-in）
if [ "$DEPLOY_TARGET_VERIFY" = "1" ]; then
    if [ -f "${ROOT}/scripts/verify-deploy-target.sh" ]; then
        TARGET_ARGS=(--env "$ENV" --base "$TARGET_BASE" --domain "$TARGET_DOMAIN" --approve-prod "$APPROVE_PROD")
        [ -f "${ROOT}/dist/index.html" ] && TARGET_ARGS+=(--artifact "${ROOT}/dist")
        if bash "${ROOT}/scripts/verify-deploy-target.sh" "${TARGET_ARGS[@]}" \
                >/tmp/deploy-target.out 2>&1; then
            gate_pass G0 "部署目标校验通过（base=${TARGET_BASE:-<空>} domain=${TARGET_DOMAIN:-<空>}）（#923）"
        else
            gate_fail G0 "部署目标校验失败（#923）：$(sed -e 's/\x1b\[[0-9;]*m//g' /tmp/deploy-target.out | grep '❌' | tr '\n' ' ' | cut -c1-300)"
        fi
    else
        gate_fail G0 "缺 scripts/verify-deploy-target.sh（#923 目标门禁已启用）：无法判断目标是否生产 ⇒ fail-closed"
    fi
else
    gate_skip G0 "目标门禁未启用（--deploy-target-verify 未传；#923 只对官网生效）"
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
