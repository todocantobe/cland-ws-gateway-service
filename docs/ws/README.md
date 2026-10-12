---
title: "WebSocket 测试客户端（原生，无 socket.io）"
summary: "cland-chat-service 原生 gorilla/websocket 测试客户端说明：连接、交互式与简化测试用法"
read_when:
  - "需要对接/调试 cland-ws-gateway-service WebSocket 时"
  - "维护 docs/ws 测试客户端时"
scope:
  - service
status: "active"
updated: "2026-10-13"
---
# WebSocket 测试客户端（原生，无 socket.io）

服务端为原生 gorilla/websocket 实现：`ws://localhost:8081/ws?cland-cid=<cid>`

## 安装依赖

```bash
npm install    # 依赖 ws (^8)
```

## 测试用例

### 简化连接测试

```bash
node simple-test.js 1          # cland-cid=1
```

### 交互式测试

```bash
node test-ws-client.js --cland-cid=1
```

## 客户端命令说明

连接成功后，支持以下交互命令：

- `send [json]` - 发送消息（缺省使用默认模板 JSON）
- `help` - 显示消息格式帮助
- `exit` - 退出

## 消息格式说明

消息为纯 JSON（直接对应服务端 `entity.Message`）：

```json
{
  "msgType": 1,
  "sessionId": "test-session",
  "msgId": "msg-001",
  "src": "U:user_001",
  "dst": "room:test-room",
  "content": "Hello World",
  "contentType": 1,
  "ts": "1745690716604",
  "status": 1
}
```

### 字段说明
- `msgType`: 1=普通消息, 2=通知, 3=确认
- `sessionId`: 会话ID
- `msgId`: 消息ID
- `src`: 发送者 (U:用户, A:客服, S:系统)
- `dst`: 接收者 (用户ID 或 room:房间名)
- `content`: 消息内容
- `contentType`: 1=文本, 2=图片, 3=文件, 520=转人工
- `ts`: 时间戳 (Unix毫秒)
- `status`: 1=新建, 2=历史, 3=离线, 4=撤回, 5=已发送, 6=已送达, 7=已读

## 注意事项
- 连接时必须提供 `cland-cid` 查询参数，否则服务器拒绝连接（400）
- 服务器默认运行在 `localhost:8081`，路径 `/ws`
- 服务端每 20 秒发送 Ping 保活，ws 库自动应答 Pong
- 服务端 60 秒未收到 Pong 则断开连接
