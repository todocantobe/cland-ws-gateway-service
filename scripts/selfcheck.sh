#!/usr/bin/env bash
# ==============================================================================
# selfcheck.sh — Jenkins 部署后自证 G5（#398 / #268 / #207）
# ==============================================================================
# 部署完成后运行；防「假通过」（health 200 ≠ 可用）。
#   G5a /health       : 期望 200（内置就绪等待重试，避免启动未完成 → 假失败；
#                         可用 SELFCHECK_WAIT_TRIES/SELFCHECK_WAIT_SLEEP 调整）
#   G5b /build.json   : Content-Type=application/json（非 SPA fallback）+ buildNo/distSha256 可读（#268）
#   G5c 核心路径 e2e  : 由 --e2e-cmd 提供（app 契约字段），失败即 FAIL
#
# 用法:
#   selfcheck.sh --base http://192.168.1.12:8080 --health-path /health --build-json
#   selfcheck.sh --base URL --health-path /health --e2e-cmd 'bash scripts/smoke.sh'
# 退出码: 0=全通过; 1=失败; 2=用法错误
# ==============================================================================
set -uo pipefail

BASE=""; HEALTH="/health"; WANT_BUILD_JSON=0; E2E_CMD=""
while [ $# -gt 0 ]; do
    case "$1" in
        --base) BASE="$2"; shift 2 ;;
        --health-path) HEALTH="$2"; shift 2 ;;
        --build-json) WANT_BUILD_JSON=1; shift ;;
        --e2e-cmd) E2E_CMD="$2"; shift 2 ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done
[ -n "$BASE" ] || { echo "用法错误: 必须 --base <URL>" >&2; exit 2; }

FAIL=0
ok()   { printf '\033[32m✅ [%s] %s\033[0m\n' "$1" "$2"; }
bad()  { printf '\033[31m❌ [%s] %s\033[0m\n' "$1" "$2" >&2; FAIL=1; }
skip() { printf '\033[33m⏭  [%s] %s\033[0m\n' "$1" "$2"; }

echo "=== 部署后自证 G5 (base=${BASE}) ==="

# G5a /health（等待就绪：容器起后应用需启动+连库+迁移+Nacos，立即探测会拿到 000 → 假失败）
WAIT_TRIES="${SELFCHECK_WAIT_TRIES:-30}"
WAIT_SLEEP="${SELFCHECK_WAIT_SLEEP:-2}"
code=""; i=0
for i in $(seq 1 "$WAIT_TRIES"); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${BASE}${HEALTH}" || echo 000)"
    [ "$code" = "200" ] && break
    sleep "$WAIT_SLEEP"
done
if [ "$code" = "200" ]; then
    ok G5 "${HEALTH} → 200（就绪于第 ${i}/${WAIT_TRIES} 次探测）"
else
    bad G5 "${HEALTH} → ${code}（非 200；等待 ${WAIT_TRIES}x${WAIT_SLEEP}s 后仍未就绪）"
fi

# G5b /build.json
if [ "$WANT_BUILD_JSON" -eq 1 ]; then
    tmp="$(mktemp)"; hdr="$(mktemp)"
    curl -s -D "$hdr" -o "$tmp" --max-time 10 "${BASE}/build.json" || true
    ctype="$(tr -d '\r' < "$hdr" | awk 'tolower($1)=="content-type:"{print $2; exit}')"
    case "$ctype" in
        application/json*) ok G5 "/build.json Content-Type=${ctype}" ;;
        *) bad G5 "/build.json Content-Type=${ctype:-空}（疑似 SPA fallback，非自证）" ;;
    esac
    if python3 - "$tmp" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding='utf-8'))
except Exception as e:
    print(f"parse-fail: {e}"); sys.exit(1)
missing = [k for k in ("buildNo", "distSha256") if not d.get(k)]
if missing:
    print("missing: " + ",".join(missing)); sys.exit(1)
print(f"buildNo={d['buildNo']} distSha256={str(d['distSha256'])[:12]}…"); sys.exit(0)
PY
    then ok G5 "/build.json 含 buildNo/distSha256"; else bad G5 "/build.json 字段缺失或非法"; fi
    rm -f "$tmp" "$hdr"
else
    skip G5 "/build.json 未要求（--build-json 未开）"
fi

# G5c 核心路径 e2e
if [ -n "$E2E_CMD" ]; then
    if bash -c "$E2E_CMD"; then ok G5 "核心路径 e2e: ${E2E_CMD}"; else bad G5 "核心路径 e2e 失败: ${E2E_CMD}"; fi
else
    skip G5 "核心路径 e2e 未提供（--e2e-cmd）"
fi

echo "================================================"
if [ "$FAIL" -eq 0 ]; then echo "✅ 部署后自证 G5 通过"; exit 0; fi
echo "❌ 部署后自证 G5 失败 → 判『假通过』" >&2
exit 1
