#!/usr/bin/env node
// =============================================================================
// fetch-nacos-env.mjs — 构建期从 Nacos 拉取环境变量，渲染成 docker --env-file
// =============================================================================
// 用途: 让 SSR/前端项目的「构建期环境变量」单一真源落在 Nacos 配置中心，
//       构建/部署流水线不再把明文密钥放进 Jenkinsfile / Job 参数 / VCS。
//
// 读取的环境变量（凭证走 Jenkins withCredentials 注入，不出现在 argv）:
//   NACOS_SERVERADDR  Nacos 地址 host:port   (默认 192.168.1.11:8848)
//   NACOS_NAMESPACE   命名空间 (public=空)   (默认 public)
//   NACOS_DATAID      配置 Data ID           (必填)
//   NACOS_GROUP       Group                  (默认 DEFAULT_GROUP)
//   NACOS_USERNAME    用户名
//   NACOS_PASSWORD    密码
//   NACOS_TIMEOUT_MS  超时毫秒               (默认 5000)
//
// 参数:
//   --out <file>       输出文件 (默认 stdout)
//   --require K1,K2    必需键（缺失即 exit 1；用于构建期快速失败）
//   --dataid <id>      覆盖 NACOS_DATAID
//   --namespace <ns>   覆盖 NACOS_NAMESPACE
//   --group <g>        覆盖 NACOS_GROUP
//   --print-keys       仅打印键名（不打印值，用于日志自证）
//
// 支持的配置内容格式:
//   KEY=VALUE 行（properties/env 风格，# 注释）或 JSON 对象 {"K":"V"}
// 兼容 Nacos 2.x(v1 API) 与 3.x(v3 API)；值不落日志。
//
// 退出码: 0=成功; 1=拉取/解析失败; 2=用法错误
// =============================================================================
import { writeFileSync } from "node:fs";
import process from "node:process";

function usage(code = 2) {
  const lines = [
    "用法: fetch-nacos-env.mjs --out <file> [--require K1,K2] [--print-keys]",
    "环境变量: NACOS_SERVERADDR NACOS_NAMESPACE NACOS_DATAID NACOS_GROUP NACOS_USERNAME NACOS_PASSWORD",
  ];
  (code === 0 ? console.log : console.error)(lines.join("\n"));
  process.exit(code);
}

const args = process.argv.slice(2);
const opt = { out: "", require: [], printKeys: false };
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === "--out") opt.out = args[++i];
  else if (a === "--require") opt.require = (args[++i] || "").split(",").map((s) => s.trim()).filter(Boolean);
  else if (a === "--dataid") process.env.NACOS_DATAID = args[++i];
  else if (a === "--namespace") process.env.NACOS_NAMESPACE = args[++i];
  else if (a === "--group") process.env.NACOS_GROUP = args[++i];
  else if (a === "--print-keys") opt.printKeys = true;
  else if (a === "-h" || a === "--help") usage(0);
  else { console.error(`未知参数: ${a}`); usage(2); }
}

const serveraddr = process.env.NACOS_SERVERADDR || "192.168.1.11:8848";
const namespace = process.env.NACOS_NAMESPACE || "public";
const dataId = process.env.NACOS_DATAID || "";
const group = process.env.NACOS_GROUP || "DEFAULT_GROUP";
const username = process.env.NACOS_USERNAME || "";
const password = process.env.NACOS_PASSWORD || "";
const timeoutMs = Number(process.env.NACOS_TIMEOUT_MS || 5000);

if (!dataId) { console.error("❌ 缺少 NACOS_DATAID（--dataid 或环境变量）"); usage(2); }

const base = serveraddr.startsWith("http") ? serveraddr.replace(/\/$/, "") : `http://${serveraddr}`;

async function req(path, init = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(`${base}${path}`, { ...init, signal: ctrl.signal });
  } finally {
    clearTimeout(t);
  }
}

async function login() {
  if (!username) return "";
  const body = new URLSearchParams({ username, password });
  const r = await req("/nacos/v1/auth/login", { method: "POST", body });
  if (!r.ok) throw new Error(`login HTTP ${r.status}`);
  const j = await r.json().catch(() => ({}));
  if (!j.accessToken) throw new Error(`login 无 accessToken: ${JSON.stringify(j).slice(0, 200)}`);
  return j.accessToken;
}

async function getConfig(token) {
  const q = (params) => new URLSearchParams(params).toString();
  const ns = namespace || "";
  // Nacos 3.x
  const v3 = `${base}/nacos/v3/client/cs/config?${q({ dataId, groupName: group, namespaceId: ns })}`;
  // Nacos 2.x
  const v1 = `${base}/nacos/v1/cs/configs?${q({ dataId, group, tenant: ns })}`;
  const withTok = (u) => (token ? `${u}&accessToken=${encodeURIComponent(token)}` : u);
  for (const url of [withTok(v3), withTok(v1)]) {
    const r = await req(url.replace(base, ""));
    if (!r.ok) continue;
    const j = await r.json().catch(() => null);
    if (!j) continue;
    // v3: {code:0,data:{content:"..."}}；v1: {content:"..."} 或纯文本
    if (typeof j.content === "string") return j.content;
    if (j.data && typeof j.data.content === "string") return j.data.content;
  }
  throw new Error(`拉取配置失败: dataId=${dataId} group=${group} namespace=${namespace || "public"}`);
}

function parseContent(content) {
  const text = content.replace(/^\uFEFF/, "");
  const trimmed = text.trim();
  if (trimmed.startsWith("{")) {
    const obj = JSON.parse(trimmed);
    return Object.entries(obj).map(([k, v]) => [k, String(v)]);
  }
  const pairs = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const body = line.replace(/^export\s+/, "");
    const eq = body.indexOf("=");
    if (eq === -1) continue;
    const key = body.slice(0, eq).trim();
    let val = body.slice(eq + 1).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    pairs.push([key, val]);
  }
  return pairs;
}

try {
  const token = await login();
  const content = await getConfig(token);
  const pairs = parseContent(content);
  if (pairs.length === 0) throw new Error("配置内容为空或无法解析");
  const keys = pairs.map(([k]) => k);
  for (const need of opt.require) {
    if (!keys.includes(need)) throw new Error(`缺少必需键: ${need}（现有: ${keys.join(",")}）`);
  }
  const rendered = pairs.map(([k, v]) => `${k}=${v}`).join("\n") + "\n";
  if (opt.out) {
    writeFileSync(opt.out, rendered, { mode: 0o600 });
    console.log(`✅ Nacos 环境变量已渲染 → ${opt.out}（${keys.length} 个键: ${keys.join(", ")}）`);
  } else {
    process.stdout.write(rendered);
  }
} catch (e) {
  console.error(`❌ fetch-nacos-env 失败: ${e instanceof Error ? e.message : e}`);
  process.exit(1);
}
