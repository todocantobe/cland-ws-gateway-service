---
title: "WebSocket 网关接入踩坑记录（PITFALLS）"
summary: "> 服务：cland-chat-service · 帧转发网关（8081）"
read_when:
  - "阅读 cland-ws-gateway-service 文档时"
  - "cland-ws-gateway-service 开发/维护时"
scope:
  - service
status: "active"
updated: "2026-10-08"
---
# WebSocket 网关接入踩坑记录（PITFALLS）

> 服务：cland-chat-service · 帧转发网关（8081）
> 定位：客户端/业务层接入时真实踩过的坑，按 症状/根因/修复/预防 记录。
> 协议见 `USAGE.md`；业务协议（protobuf Envelope + 成员列表）见 `references/biz_protocol.md`（skill: cland-ws-gateway）。

---

## P1. 请求-应答必须同一连接，否则回复必丢

**症状**：A 进程 `listen`（cid=sre_list）等回复，B 进程 `send`（cid=sre_ask）发请求；对方已回（服务端日志、对方日志都有发送记录），但 A 永远收不到。

**根因**：网关只认 cid 不认进程。回复帧投递给"信封 from 对应的在线连接"。发请求的是一次性连接（发完即断），回复到达时该连接已离线 → 静默丢失。listen/send 分两个 cid 或两条连接拼链路，都是这个结局。

**修复**：请求与等待必须在同一连接内。`scripts/biz.py ask --cid X --to Y --room R`（连接 → 发 member_list → 等 direct 回复 → 退出）。同理，任何"发消息等响应"的客户端都要用同一连接。

**预防**：协议层约定"应答投递目标 = 请求帧信封 from 的当前连接"；客户端请求-应答模式禁止拆两条连接。

---

## P2. cid 全网关唯一：同 cid 新连接顶替旧连接（自己踢自己）

**症状**：长驻 `listen`（cid=sre_list）+ 一次性 `send`（同 cid=sre_list）——listen 连接被顶替，后续帧全丢。

**根因**：网关按 cid 路由，重复连接直接顶替旧连接（旧连接按离线清理）。这是设计（重连自动接管），但同 cid 收发并存时就是自杀。

**修复**：收发职责分离用**不同 cid**（长驻 listen 一个 cid，回话用 `<cid>_reply` 独立连接，如 `dba`/`dba_reply`）；或请求-应答走同一连接（见 P1）。

**预防**：任何进程开连接前先自查 cid 是否已被其他进程占用；回话连接统一 `<cid>_reply` 命名。

---

## P3. 房间广播不含发送者：自己的声明自己收不到

**症状**：广播 `member_join` 声明自己入房，自己从未在成员表里看到自己；对账列表缺自己。

**根因**：网关设计——房间广播只投递给"除发送者外"的成员（防回声风暴）。业务层成员列表若依赖"收到 join 广播才收录"，自己永远缺席。

**修复**：入房广播声明的同时，**本地成员表直接加入自己**（不依赖回显）。参考实现 `scripts/biz_agent.py` announce()。

**预防**：协议文档明确"声明者自收录"；对账（member_list 拉取）作为最终一致性兜底。

---

## P4. 工具解析 join_ack/leave_ack/pong 等无 payload 帧会崩

**症状**：`biz.py decode --from-json` 挂在 `listen --exec` 上，收到 join_ack 后进程 exit 3，链路中断。

**根因**：join_ack/leave_ack/pong 帧**没有 payload**，`payload_hex` 字段为空 → `bytes.fromhex("")` 解出空字节 → `Envelope.FromString(b"")` 抛异常。

**修复**：`--from-json` 遇空 `payload_hex` 直接 `continue` 跳过（无 payload 帧不属于业务信封）。

**预防**：任何"把网关帧当业务信封解析"的工具，必须先判 payload 存在性；测试用例覆盖 join_ack/leave_ack/pong 三种空载荷帧。

---

## P5. protobuf 7.x python：repeated 字段不能 setattr，保留字 from 不再映射 from_

**症状**：`setattr(msg, "members", [...])` 抛 `Assignment not allowed to map, or repeated field`；`msg.from_` 抛 `AttributeError`。

**根因**：python protobuf 7.x 生成代码——repeated 字段只能 `extend()/append()`；保留字字段 `from` 不再映射为 `from_`，需 `setattr/getattr(msg, "from")`（旧版映射 from_ 的写法失效）。

**修复**：封装 `_setf(msg, **kw)`：list/tuple 值走 `getattr(msg, k).extend(v)`，其余 `setattr`；读侧统一 `getattr(msg, "from", "")`。

**预防**：协议字段命名避开 python 关键字（本协议 from 是历史决定，工具层已兜底）；升级 protobuf 大版本后跑一遍 round-trip 回归。

---

## P6. 长驻监听窗口要覆盖对方回复时间

**症状**：12s 的 `listen` 超时退出，恰好错过对方 13s 时的回复；服务端日志显示连接活动，客户端却"没等到"。

**根因**：agent 间对话没有约定的响应时限，监听窗口比对方思考+回复时间短。

**修复**：agent 互通场景监听挂 30s+；或约定"收到必回"并让请求方带超时重试。

**预防**：请求-应答加超时/重试语义；排查"没收到"先看服务端日志（`/tmp/cland-chat-svc.out` 的 New connection/Connection removed 时间线），别只盯客户端。

---

## P7. 运维：pkill/pgrep -f 匹配到自身命令行

**症状**：`pkill -f 'biz_agent.py --cid sre_agent'` 执行后**整个 shell 消失**，无输出（把自己杀了）。

**根因**：`-f` 匹配完整命令行，当前 bash -c 的命令行里包含同样字符串，自匹配自杀。

**修复**：`pgrep -f '[b]iz_agent.py --cid sre_agent'`（正则字符类技巧，自身命令行不含 `[b]` 原文即不匹配）；或先 `pgrep` 拿 PID 再精确 kill。

**预防**：凡 `pkill -f` 带脚本路径/参数的，一律先 pgrep 确认 PID 列表，排除自身后再杀。

---

## 关联

- 协议规范：`USAGE.md`
- 业务协议（protobuf Envelope / 成员列表 / 心跳剔除）：skill `cland-ws-gateway/references/biz_protocol.md`
- 客户端工具：`scripts/gw.py`（帧层）、`scripts/biz.py`（业务编解码 + ask 对账）、`scripts/biz_agent.py`（常驻 agent 参考实现）
