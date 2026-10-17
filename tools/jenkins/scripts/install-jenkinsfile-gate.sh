#!/usr/bin/env bash
# =============================================================================
# install-jenkinsfile-gate.sh — 给仓库安装 Jenkinsfile 漂移门禁
# =============================================================================
# 做三件事（幂等，可重复执行 = 升级固化资产）:
#   1. 固化生成器 + 模板到 <repo>/tools/jenkins/（自包含，CI 离线可跑）
#   2. 安装检查脚本 <repo>/scripts/check-jenkinsfile.sh
#   3. 打印 gate.yaml / CI 接线片段（pre-commit 与 CI 同一断言）
#
# 用法:
#   bash install-jenkinsfile-gate.sh [仓库根] [--force]
#   bash install-jenkinsfile-gate.sh --check [仓库根]   # 只校验漂移，不写入(#456)
#   （默认仓库根 = 当前目录）
# =============================================================================
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-$(pwd)}"
FORCE=0
[[ "${2:-}" == "--force" ]] && FORCE=1

# --check：校验「已固化副本 == 真源」（真源优先本机 skill；CI 离线退回 manifest）（#456）
if [[ "${1:-}" == "--check" ]]; then
    TARGET="${2:-$(pwd)}"
    VENDOR="${TARGET}/tools/jenkins"
    MANIFEST="${VENDOR}/VENDORED.sha256"
    drift=0
    cmp_one() { # cmp_one <src(真源)> <dst(副本)>
        local src="$1" dst="$2"
        if [ ! -f "$dst" ]; then echo "❌ 缺副本: $dst"; drift=1; return; fi
        if ! cmp -s "$src" "$dst"; then echo "❌ 漂移: $dst"; drift=1; fi
    }
    TRUTH="${JENKINS_SKILL_DIR:-}"
    if [ -z "$TRUTH" ] && [ -d "${HOME}/.agents/skills/jenkins" ]; then
        TRUTH="${HOME}/.agents/skills/jenkins"
    fi
    if [ -n "$TRUTH" ] && [ -d "$TRUTH" ]; then
        echo "── Jenkinsfile 门禁「真源↔副本」漂移检查（真源=$TRUTH）──"
        cmp_one "${TRUTH}/scripts/generate-jenkinsfile.py" "${VENDOR}/scripts/generate-jenkinsfile.py"
        cmp_one "${TRUTH}/scripts/check-jenkinsfile.sh"     "${TARGET}/scripts/check-jenkinsfile.sh"
        cmp_one "${TRUTH}/scripts/deploy-gate.sh"           "${TARGET}/scripts/deploy-gate.sh"
        cmp_one "${TRUTH}/scripts/selfcheck.sh"             "${TARGET}/scripts/selfcheck.sh"
        cmp_one "${TRUTH}/scripts/test-branch-gate.sh"      "${TARGET}/scripts/test-branch-gate.sh"
        cmp_one "${TRUTH}/scripts/fetch-nacos-env.mjs"      "${TARGET}/scripts/fetch-nacos-env.mjs"
        for t in "${TRUTH}"/templates/*; do
            cmp_one "$t" "${VENDOR}/templates/$(basename "$t")"
        done
        for t in "${VENDOR}"/templates/*; do
            [ -e "$t" ] || continue
            if [ ! -f "${TRUTH}/templates/$(basename "$t")" ]; then
                echo "❌ 多余副本(真源已无): $t"; drift=1
            fi
        done
    elif [ -f "$MANIFEST" ]; then
        # CI 离线（GitHub runner 触不到内网 skill/hub）：用安装期生成的 sha256 清单
        echo "── Jenkinsfile 门禁「真源↔副本」漂移检查（真源不可达，退回 manifest=$MANIFEST）──"
        while read -r sha path; do
            [ -z "$sha" ] && continue
            case "$sha" in \#*) continue;; esac
            actual="$(sha256sum "${TARGET}/${path}" 2>/dev/null | cut -d' ' -f1)"
            if [ -z "$actual" ]; then echo "❌ 缺副本: ${TARGET}/${path}"; drift=1
            elif [ "$actual" != "$sha" ]; then echo "❌ 漂移: ${TARGET}/${path}"; drift=1; fi
        done < "$MANIFEST"
    else
        echo "❌ 真源不可达且无 manifest ⇒ 无法判定漂移（视同失败）" >&2
        echo "   修复：bash install-jenkinsfile-gate.sh \"${TARGET}\" 生成 manifest，或设 JENKINS_SKILL_DIR=<skill目录>" >&2
        exit 1
    fi
    # #576：VENDORED.sha256 自校验——清单必须与仓内副本逐项一致（防"清单是旧的"）
    if [ -f "$MANIFEST" ]; then
        echo "── VENDORED.sha256 自校验（清单↔仓内副本）──"
        while read -r sha path; do
            [ -z "$sha" ] && continue
            case "$sha" in \#*) continue;; esac
            actual="$(sha256sum "${TARGET}/${path}" 2>/dev/null | cut -d' ' -f1)"
            if [ -z "$actual" ]; then echo "❌ 清单项缺副本: ${path}"; drift=1
            elif [ "$actual" != "$sha" ]; then echo "❌ 清单不符: ${path}"; drift=1; fi
        done < "$MANIFEST"
    else
        echo "❌ 缺 manifest: ${MANIFEST}（无法自校验，#576）" >&2; drift=1
    fi
    if [ "$drift" -eq 0 ]; then echo "✅ 无漂移：副本与真源一致，manifest 自校验通过"; exit 0; fi
    echo ""
    echo "修正：bash install-jenkinsfile-gate.sh \"${TARGET}\" --force" >&2
    exit 1
fi

if [ ! -e "${TARGET}/.git" ]; then
    echo "❌ 不是 git 仓库根（缺 .git）: ${TARGET}" >&2
    exit 1
fi

install_file() { # install_file <src> <dst>
    local src="$1" dst="$2"
    mkdir -p "$(dirname "$dst")"
    if [ -f "$dst" ] && [ "$FORCE" -eq 0 ]; then
        echo "kept   (本地已有): $dst"
        return
    fi
    cp "$src" "$dst"
    echo "created: $dst"
}

VENDOR="${TARGET}/tools/jenkins"
install_file "${SKILL_DIR}/scripts/generate-jenkinsfile.py" "${VENDOR}/scripts/generate-jenkinsfile.py"
install_file "${SKILL_DIR}/scripts/install-jenkinsfile-gate.sh" "${VENDOR}/scripts/install-jenkinsfile-gate.sh"

# 模板整体固化（生成器按 ../templates 定位）
mkdir -p "${VENDOR}/templates"
cp -f "${SKILL_DIR}"/templates/* "${VENDOR}/templates/" 2>/dev/null || true
echo "synced : ${VENDOR}/templates/ ($(ls "${VENDOR}/templates" | wc -l) files)"

install_file "${SKILL_DIR}/scripts/check-jenkinsfile.sh" "${TARGET}/scripts/check-jenkinsfile.sh"
install_file "${SKILL_DIR}/scripts/deploy-gate.sh" "${TARGET}/scripts/deploy-gate.sh"
install_file "${SKILL_DIR}/scripts/selfcheck.sh" "${TARGET}/scripts/selfcheck.sh"
install_file "${SKILL_DIR}/scripts/test-branch-gate.sh" "${TARGET}/scripts/test-branch-gate.sh"
# frontend-ssr-nacos: 构建期从 Nacos 拉取环境变量（取不到即 fail-closed）
install_file "${SKILL_DIR}/scripts/fetch-nacos-env.mjs" "${TARGET}/scripts/fetch-nacos-env.mjs"
chmod +x "${TARGET}/scripts/check-jenkinsfile.sh" "${TARGET}/scripts/deploy-gate.sh" "${TARGET}/scripts/selfcheck.sh" "${TARGET}/scripts/test-branch-gate.sh" "${TARGET}/scripts/fetch-nacos-env.mjs"

# 记录固化来源，便于审计版本
HUB_REMOTE="$(git -C "${SKILL_DIR}" remote get-url origin 2>/dev/null || true)"
HUB_COMMIT="$(git -C "${SKILL_DIR}" rev-parse HEAD 2>/dev/null || true)"
{
    echo "# 由 jenkins 技能 install-jenkinsfile-gate.sh 固化，勿手改（重跑脚本可升级）"
    echo "source: ~/.agents/skills/jenkins"
    [ -n "$HUB_REMOTE" ] && echo "mirror: ${HUB_REMOTE}"
    [ -n "$HUB_COMMIT" ] && echo "mirror_commit: ${HUB_COMMIT}"
    echo "date: $(date +%F)"
} > "${VENDOR}/VENDORED.md"

# 生成 sha256 清单（CI 离线判漂移用；与真源同源写入）
gen_manifest() {
    local rel
    for rel in "tools/jenkins/scripts/generate-jenkinsfile.py" \
               "scripts/check-jenkinsfile.sh" "scripts/deploy-gate.sh" \
               "scripts/selfcheck.sh" "scripts/test-branch-gate.sh" \
               "scripts/fetch-nacos-env.mjs"; do
        [ -f "${TARGET}/${rel}" ] && sha256sum "${TARGET}/${rel}" | sed "s#  ${TARGET}/#  #"
    done
    local t
    for t in "${VENDOR}"/templates/*; do
        [ -f "$t" ] && sha256sum "$t" | sed "s#  ${VENDOR}/templates/#  tools/jenkins/templates/#"
    done
}
gen_manifest | sort -k2 > "${VENDOR}/VENDORED.sha256"
echo "written: ${VENDOR}/VENDORED.sha256 ($(wc -l < "${VENDOR}/VENDORED.sha256") files)"

cat <<EOF

✅ Jenkinsfile 门禁资产已安装 → ${TARGET}
   生成器: tools/jenkins/scripts/generate-jenkinsfile.py（自包含）
   检查器: scripts/check-jenkinsfile.sh
   CI 依赖: 需安装 pyyaml（生成器解析 jenkins-config.yml 用）

── gate.yaml 追加（pre-commit 与 CI 共用同一断言；两项都要接）──
  jenkinsfile-drift:
    cmd: "bash scripts/check-jenkinsfile.sh"
    on:
      - "Jenkinsfile"
      - "jenkins-config.yml"
      - "tools/jenkins/**"
    stage: fast
  vendored-sync:            # #456：本仓 vendored 副本 == 真源（不一致即红，不可静默 PASS）
    cmd: "bash ~/.agents/skills/jenkins/scripts/install-jenkinsfile-gate.sh --check . || bash tools/jenkins/scripts/install-jenkinsfile-gate.sh --check ."
    on:
      - "tools/jenkins/**"
      - "scripts/check-jenkinsfile.sh"
      - "scripts/deploy-gate.sh"
      - "scripts/selfcheck.sh"
      - "scripts/test-branch-gate.sh"
    stage: fast

── .github/workflows/gate.yml 追加（若用 commit-gate 可并入既有 job）──
      - name: 安装 pyyaml (Jenkinsfile 门禁依赖)
        run: pip install pyyaml
      - name: Jenkinsfile 漂移门禁
        run: bash scripts/check-jenkinsfile.sh

── 验证 ──
  bash scripts/check-jenkinsfile.sh                 # 绿例
  echo '# drift' >> Jenkinsfile && bash scripts/check-jenkinsfile.sh; echo "exit=\$?"  # 红例
  git checkout -- Jenkinsfile
EOF
