---
title: "WebSocket 帧转发网关使用文档（USAGE）"
summary: "> 服务：cland-chat-service · WS 端口 **8081** · 路径 `/ws`"
read_when:
  - "阅读 cland-ws-gateway-service 文档时"
  - "cland-ws-gateway-service 开发/维护时"
scope:
  - service
status: "active"
updated: "2026-10-08"
---
# WebSocket 帧转发网关使用文档（USAGE）

> 服务：cland-chat-service · WS 端口 **8081** · 路径 `/ws`
> 定位：**游戏网关**——转发帧，业务逻辑层协议完全自定义（网关零业务解析）
> 版本：v1（2026-08-22，commit 75a93a7 原生协议重构后）

---

## 1. 快速开始

```bash
# 连接（cid 为用户标识，必填；缺失返回 HTTP 401）
ws://127.0.0.1:8081/ws?cid=user_001
```

三个最常用帧（均为二进制）：

| 场景 | 客户端发送 | 网关行为 |
|------|-----------|---------|
| 定向发给某用户 | `[01][dstLen][dst][payload]` | 目标在线 → 投递 `[01][srcLen][src][payload]`；离线 → 错误帧 `[FF][02]` |
| 广播到房间 | `[02][roomLen][room][payload]` | 投递给房间内**除发送者外**的所有在线成员 |
| 加入房间 | `[03][roomLen][room]` | 回确认帧 `[03][roomLen][room]` |

---

## 2. 连接与认证

- **URL**：`ws://host:8081/ws?cid=<cid>`
- `cid`：任意 UTF-8 字符串，网关内唯一（重复连接会覆盖旧连接，旧连接被顶替后按离线清理）
- 认证失败：HTTP 401 `{"code":40010010001,"message":"missing cid"}`
- 跨域：全源放行（`CheckOrigin` 恒 true，适合游戏客户端）

## 3. 帧协议规范

所有帧为 **WebSocket BinaryMessage**，小端序。目标（dst/room/src）为 UTF-8 字符串，长度字段 u8（≤255）。

### 3.1 客户端 → 网关（请求帧）

| 类型 | 名称 | 格式 | 说明 |
|------|------|------|------|
| `0x01` | DIRECT 定向转发 | `[01][dstLen u8][dst][payload]` | 转发给单个在线用户；离线回 `[FF][02]` |
| `0x02` | ROOM 房间广播 | `[02][roomLen u8][room][payload]` | 转发给房间内所有**其他**成员；房间空则静默丢弃 |
| `0x03` | JOIN 加入房间 | `[03][roomLen u8][room]` | 幂等；回确认帧 `[03][room]` |
| `0x04` | LEAVE 离开房间 | `[04][roomLen u8][room]` | 幂等；回确认帧 `[04][room]` |
| `0x05` | PING 应用心跳 | `[05]` | 回 `[05]` pong（可选，协议级心跳已兜底） |

### 3.2 网关 → 客户端（投递帧）

| 类型 | 名称 | 格式 | 说明 |
|------|------|------|------|
| `0x01` | 定向投递 | `[01][srcLen u8][src][payload]` | `src` 为发送方 cid，`payload` 为发送方原始载荷 |
| `0x02` | 房间投递 | `[02][srcLen u8][src][payload]` | 同上，来自房间广播 |
| `0x03` | join 确认 | `[03][roomLen u8][room]` | |
| `0x04` | leave 确认 | `[04][roomLen u8][room]` | |
| `0x05` | PONG | `[05]` | 应答 `[05]` |
| `0xFF` | 错误 | `[FF][code u8][msgLen u8][msg]` | 见错误码 |

### 3.3 错误码（`0xFF` 帧的 code）

| code | 含义 | 触发场景 |
|------|------|---------|
| `0x01` | 帧头非法 | 空帧/长度截断/空目标 |
| `0x02` | 定向目标离线 | DIRECT 目标不在线 |
| `0x03` | 未知帧类型 | 首字节不是 0x01~0x05 |
| `0x04` | 参数错误 | 预留 |

## 4. 心跳与离线判定

- **协议级**：服务端每 **25s** 发送 WS 控制帧 ping，客户端（浏览器/游戏引擎）自动回 pong，**零业务字节开销**
- **读超时**：客户端 **45s** 无任何帧（含 pong）→ 判定离线，连接关闭、注册表清理（含房间成员资格）
- 应用层 `[05]` PING 可选：适用于需要业务层存活感知的场景

## 5. 房间语义

- 房间由客户端自由命名（`room` 字符串），无创建/销毁概念：首个成员加入即存在，最后成员离开即消失
- 广播**不含发送者**（不回显）；需要回显的客户端自行处理
- 断线自动退出全部房间（服务端清理，无需显式 LEAVE）

## 6. 客户端示例

### Python（websockets）

```python
import asyncio, websockets

async def main():
    ws = await websockets.connect("ws://127.0.0.1:8081/ws?cid=user_001")
    await ws.send(bytes([0x03, 5]) + b"roomA")                     # JOIN roomA
    print((await ws.recv()).hex())                                 # 03 05 roomA（确认）
    await ws.send(bytes([0x02, 5]) + b"roomA" + b"\x01\x02\x03")   # 广播 payload
    async for frame in ws:
        t = frame[0]
        if t in (0x01, 0x02):                                      # 投递帧
            slen = frame[1]; src = frame[2:2+slen].decode(); pl = frame[2+slen:]
            print("from", src, "payload", pl.hex())
        elif t == 0xFF:                                            # 错误帧
            print("err", frame[1], frame[3:3+frame[2]].decode())

asyncio.run(main())
```

### Go（gorilla/websocket）

```go
import "github.com/gorilla/websocket"

conn, _, err := websocket.DefaultDialer.Dial("ws://127.0.0.1:8081/ws?cid=user_001", nil)
// 加入房间
conn.WriteMessage(websocket.BinaryMessage, append([]byte{0x03, 5}, "roomA"...))
// 定向发送
frame := append([]byte{0x01, 8}, "user_002"...)
frame = append(frame, []byte("hello")...)
conn.WriteMessage(websocket.BinaryMessage, frame)
```

## 7. 性能与压缩说明

- **零拷贝**：网关解析帧头后，payload 直接引用读缓冲切片转发，不做拷贝、不做序列化
- **开销**：每帧仅 2~4 字节头（类型 + 目标长度 + 目标），payload 原样
- **写并发**：每连接写锁串行化（gorilla 单 writer 约束），投递与心跳互不干扰
- 目标长度 u8 上限 255 字节：长 ID 场景可自行在 payload 内做 ID 映射（逻辑层自定义）

## 8. 验证

```bash
# 单元测试（协议解析/构建，16 用例）
go test ./core/infrastructure/delivery/websocket/...

# e2e（认证/定向/房间/join-leave/ping/心跳 12 项）
python3 /tmp/e2e_gateway.py
```

## 9. 相关

- 协议实现：`core/infrastructure/delivery/websocket/gateway/`
- 连接注册表：`core/infrastructure/delivery/websocket/connection/`
- **接入踩坑记录**：`docs/websocket/PITFALLS.md`（请求-应答同连接 / cid 顶替 / 广播不含发送者 等 7 条，客户端接入前必读）
- kanban：BAS-ISSUE-002（Closed，需求变更）、[FEATURE] cland-chat-service 原生 WS 帧转发网关（Done）
