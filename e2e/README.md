---
title: "端到端测试（e2e）"
summary: "cland-chat-service 端到端测试说明：目录结构、运行前置与用例清单"
read_when:
  - "运行/维护 cland-chat-service e2e 测试时"
  - "新增用户通信端到端用例时"
scope:
  - service
status: "active"
updated: "2026-10-13"
---
# 端到端测试（e2e）

单测与端到端严格分开：单测 `*_test.go` 与源码同目录；端到端在本目录。

## 结构

```
e2e/
├── ws_user_chat.py       # ★ 用户通信 WebSocket e2e（Python）
├── api_test.http         # HTTP 接口 e2e（IDE/httpyac 运行）
├── http-client.env.json  # 环境变量 {{host}}
└── README.md
```

## 运行前置

1. 启动服务（HTTP :8080 / WS :8081）
2. 初始化数据库（执行 `docs/sql/init.sql`）
3. WS e2e 依赖 Python 库：`pip3 install websocket-client`

## 用户通信 e2e（WebSocket）

```bash
make e2e
# 或
python3 e2e/ws_user_chat.py [--host 127.0.0.1] [--port 8081]
```

覆盖场景：

| 用例 | 说明 |
| ---- | ---- |
| A/B 连接 | 原生 ws 直连 `/ws?cland-cid=xxx` |
| A → B 直发 | 接收方在线，B 收到 `{code:200,msg:success,data:{...}}` |
| B → A 回发 | 双向通信 |
| 已读回执 | msgType=3 ACK，状态流转无异常 |
| 离线用户 | 接收方未连接，不报错（落库离线态） |
| 非法 JSON | 返回统一错误码 `50010010000` |
| 缺 cland-cid | 服务端拒绝连接 |

## HTTP 接口 e2e

在 IDE 中打开 `api_test.http` 运行（JetBrains HTTP Client / VS Code REST Client / httpyac）。

覆盖：健康检查、初始化用户、HTTP 发消息、查询会话消息。
