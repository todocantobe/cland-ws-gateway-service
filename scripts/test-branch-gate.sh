#!/usr/bin/env bash
# =============================================================================
# test-branch-gate.sh — 发布分支门禁的「可跑失败」验收（#335 / CI-054 / #456）
#
# 设计（现行 · 单一真源）：**guard 落 `deploy-gate.sh`（G1）**，Jenkinsfile 只负责
#   **调用**它（缺脚本即 fail-closed）。⇒ 本测试断言的是**这一层**，不是 Jenkinsfile 内联守卫。
#   （历史：#456 更正 —— 旧版断言"Jenkinsfile 内联守卫"，那是被取代的设计。）
# 用法: scripts/test-branch-gate.sh [Jenkinsfile] [deploy-gate.sh]
# 退出码: 0 = 门禁在位且判定正确；1 = 门禁缺失或判定错误
# =============================================================================
set -euo pipefail
JF="${1:-Jenkinsfile}"
GATE="${2:-scripts/deploy-gate.sh}"
[ -f "$JF" ] || { echo "FAIL: 找不到 $JF"; exit 1; }

# 与 deploy-gate.sh G1 保持一致的判定（release/* · hotfix/* · master|main 允许，其余拒）
RE_RELEASE='^release/.*$'
RE_HOTFIX='^hotfix/.*$'
RE_MAIN='^(master|main)$'
allowed() { [[ "$1" =~ $RE_RELEASE || "$1" =~ $RE_HOTFIX || "$1" =~ $RE_MAIN ]]; }

fail=0
echo "== ① 门禁在位断言（层 = deploy-gate.sh；$JF 只调用）=="
# 1) Jenkinsfile 必须调用 deploy-gate.sh，且缺脚本时 fail-closed
call=$(grep -c "deploy-gate.sh" "$JF" || true)
echo "   Jenkinsfile 调用 deploy-gate.sh 出现 $call 处（期望 ≥1）"
[ "$call" -ge 1 ] || { echo "   [FAIL] Jenkinsfile 未调用 deploy-gate.sh（门禁未接入）"; fail=1; }
fc=$(grep -c "缺部署门禁脚本" "$JF" || true)
echo "   缺脚本 fail-closed 提示出现 $fc 处（期望 ≥1）"
[ "$fc" -ge 1 ] || { echo "   [FAIL] 缺 fail-closed（门禁脚本缺失应硬失败，不得静默跳过）"; fail=1; }
# 2) deploy-gate.sh 含 G1 守卫（release/*→develop 血缘候选；hotfix/*；其余拒绝）
if [ -f "$GATE" ]; then
    rel=$(grep -cE 'release/\*\)' "$GATE" || true)
    anc=$(grep -cE 'require_ancestor|merge-base --is-ancestor' "$GATE" || true)
    rej=$(grep -c "不可部署分支" "$GATE" || true)
    echo "   $GATE: release/*=$rel · 血缘断言=$anc · 拒绝子=$rej（期望各 ≥1）"
    { [ "$rel" -ge 1 ] && [ "$anc" -ge 1 ] && [ "$rej" -ge 1 ]; } \
        || { echo "   [FAIL] deploy-gate.sh 缺 G1 守卫（release/hotfix 血缘 + 拒绝子）"; fail=1; }
else
    echo "   [FAIL] 缺 $GATE（guard 唯一真源层不存在）"; fail=1
fi
[ "$fail" -eq 0 ] && echo "   [PASS] 门禁接入正确层（Jenkinsfile 调用 deploy-gate.sh + fail-closed；G1 在位）"

echo "== ② 分支判定（非受控必须被拒）=="
declare -a ALLOW=( "main" "master" "release/20261007" "hotfix/20261011" )
declare -a DENY=( "feat/146-g4-identity-outbox" "feat/335-jenkinsfile-branch-gate" "develop" "release" "maintenance" "release-" )
for b in "${ALLOW[@]}"; do
  if allowed "$b"; then echo "   [PASS] 允许: $b"; else echo "   [FAIL] 误拒: $b"; fail=1; fi
done
for b in "${DENY[@]}"; do
  if allowed "$b"; then echo "   [FAIL] 漏放(应被拒): $b"; fail=1; else echo "   [PASS] 拒绝: $b"; fi
done

echo "----------------------------------------------------------------"
if [ "$fail" -eq 0 ]; then echo "BRANCH-GATE PASS（门禁在位于 deploy-gate.sh 层 · 非受控分支被拒）"; exit 0
else echo "BRANCH-GATE FAIL"; exit 1; fi
