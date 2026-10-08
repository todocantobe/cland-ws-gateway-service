---
title: "Cland Chat Service"
summary: "基于Go语言的客服聊天系统，采用简洁架构(Clean Architecture)设计。"
read_when:
  - "阅读 cland-ws-gateway-service 文档时"
  - "cland-ws-gateway-service 开发/维护时"
scope:
  - service
status: "active"
updated: "2026-10-08"
---
# Cland Chat Service

基于Go语言的客服聊天系统，采用简洁架构(Clean Architecture)设计。

## 项目结构 (更新)

```
cland-chat-service/
├── core/                 # 核心业务代码
│   ├── domain/           # 领域层
│   │   ├── entity/       # 领域实体
│   │   └── repository/   # 仓储接口
│   ├── usecase/          # 用例层(业务逻辑)
│   └── infrastructure/   # 基础设施层
│       ├── delivery/     # 交付层(HTTP/WebSocket)
│       └── repository/   # 仓储实现(Memory/SQLite)
├── common/               # 公共组件
│   ├── constants/        # 常量定义
│   ├── errors/           # 错误定义
│   └── utils/            # 工具函数
├── conf/                 # 配置文件
├── docs/                 # 文档
├── http/                 # HTTP测试脚本
├── sql/                  # SQL初始化脚本
├── main.go               # 程序入口
└── go.mod                # Go模块文件
```

## 架构设计

### 简洁架构分层

1. **领域层(Domain)**
   - 定义核心业务实体(Entity)和仓储接口(Repository)
   - 包含业务规则和领域逻辑
   - 示例: `core/domain/entity/chat.go`, `core/domain/repository/chat_repository.go`

2. **用例层(UseCase)**
   - 实现具体业务逻辑
   - 协调领域对象和仓储
   - 示例: `core/usecase/chat_usecase.go`

3. **基础设施层(Infrastructure)**
   - 实现仓储接口(SQLite/Memory)
   - 处理外部交互(HTTP/WebSocket)
   - 示例: `core/infrastructure/repository/sqlite_repository.go`

4. **交付层(Delivery)**
   - 处理外部请求和响应
   - 转换DTO和领域对象
   - 示例: `core/infrastructure/delivery/http/handler/chat_handler.go`

### 依赖关系

```
Delivery → UseCase → Domain
            ↑
Infrastructure → Domain
```

## 核心功能

- 实时聊天(WebSocket)
- 会话管理
- 消息存储(SQLite/Memory)
- 客服分配
- REST API接口

## WebSocket 帧转发网关（原生协议）

极简 WS 网关监听 **8081**（`/ws`），原生 WebSocket 二进制帧转发：**网关只解析 1 字节类型 + 路由目标，payload 原样转发（零 JSON、零业务解析）**，业务逻辑层协议完全自定义。适用于游戏帧转发等高频场景。

### 连接

```
ws://host:8081/ws?cid=user_001
```

- `cid` 必填（用户标识），缺失返回 HTTP 401
- 心跳为 WebSocket 协议级控制帧 ping/pong（服务端每 25s 发 ping，零业务字节开销）；客户端 45s 无帧即判定离线

### 帧格式（二进制，小端）

**客户端 → 网关**：

| 类型 | 含义 | 格式 |
|------|------|------|
| `0x01` | 定向转发 | `[01][dstLen u8][dst][payload]` |
| `0x02` | 房间广播 | `[02][roomLen u8][room][payload]`（投递房间内除发送者外所有成员） |
| `0x03` | 加入房间 | `[03][roomLen u8][room]` |
| `0x04` | 离开房间 | `[04][roomLen u8][room]` |
| `0x05` | 应用 PING | `[05]` |

**网关 → 客户端**：

| 类型 | 含义 | 格式 |
|------|------|------|
| `0x01` | 定向投递 | `[01][srcLen u8][src][payload]` |
| `0x02` | 房间投递 | `[02][srcLen u8][src][payload]` |
| `0x03` | join 确认 | `[03][roomLen u8][room]` |
| `0x04` | leave 确认 | `[04][roomLen u8][room]` |
| `0x05` | PONG | `[05]` |
| `0xFF` | 错误 | `[FF][code u8][msgLen u8][msg]` |

错误码：`0x01` 帧头非法 · `0x02` 定向目标离线 · `0x03` 未知帧类型 · `0x04` 参数错误

> 路由目标（dst/room）为 UTF-8 字符串，长度 u8（≤255）；payload 为原始字节，长度不限（单帧 ≤ 受 WebSocket 帧限制）。

### 客户端示例（Python）

```python
import asyncio, websockets

async def main():
    ws = await websockets.connect("ws://127.0.0.1:8081/ws?cid=user_001")
    # 加入房间 + 广播一帧
    await ws.send(bytes([0x03, 5]) + b"roomA")                 # JOIN roomA
    await ws.send(bytes([0x02, 5]) + b"roomA" + b"\x01\x02\x03")  # ROOM 广播 payload
    # 定向发送
    await ws.send(bytes([0x01, 8]) + b"user_002" + b"hello")
    async for frame in ws:
        print(frame.hex())  # 0x01/0x02 投递帧 | 0x03/0x04 确认 | 0x05 pong | 0xFF 错误

asyncio.run(main())
```

### 一键接入工具（http/gw.py）

不想手搓帧，直接用 `http/gw.py`（Python3 + websockets>=11，CLI + 库双用）：

```bash
# 连通性测试
python3 http/gw.py probe --cid agent_a
# 加入/离开房间
python3 http/gw.py join  --cid agent_a --room lab
python3 http/gw.py leave --cid agent_a --room lab
# 定向发送（文本 / 二进制）
python3 http/gw.py send --cid agent_a --to agent_b --data '{"cmd":"hi"}'
python3 http/gw.py send --cid agent_a --to agent_b --hex bb02
# 房间广播
python3 http/gw.py broadcast --cid agent_a --room lab --data hello
# 长驻监听（JSON 行输出；--exec 可挂外部处理命令，帧信息走 stdin）
python3 http/gw.py listen --cid agent_b --join lab --exec "bash /tmp/handler.sh"
```

- 输出一律 JSON 行（`{"type":"direct|room|join_ack|error","src":...,"payload":...}`），错误帧退出码 3
- 二进制 payload 用 `--hex`/`--file`/`--stdin`（shell 直接传 `$'\xBB'` 会被 UTF-8 破坏）
- 自带 20s 应用级心跳保活；库模式 `from gw import WSGateway`
- 单一来源：`~/.agents/skills/cland-ws-gateway/scripts/gw.py`（本副本改动以全局为准）

## 技术栈

### 后端技术

- **Go 1.24**
  - 高性能、并发支持
  - 标准库丰富

- **Gin Web框架**
  - 高性能HTTP框架
  - 中间件支持

- **SQLite**
  - 轻量级嵌入式数据库
  - 支持事务
  - 本地存储方案

- **WebSocket**
  - 实时双向通信
  - 心跳检测
  - 连接管理

### 开发工具

- **Git** - 版本控制
- **Docker** - 容器化部署
- **Make** - 构建工具
- **Swagger** - API文档

## 如何使用

1. 安装依赖
```bash
go mod download
```

2. 初始化数据库
```bash
sqlite3 chat.db < sql/init.sql
```

3. 运行服务
```bash
go run main.go
```

## 配置说明

配置文件位于 `conf/` 目录:

- `app.ini` - 应用配置
- `config.yaml` - 详细配置

环境变量覆盖:
- `PORT` - 服务端口(默认8080)
- `DB_PATH` - SQLite数据库路径(默认chat.db)

## 贡献指南

1. Fork 项目
2. 创建特性分支
3. 提交更改
4. 推送到分支
5. 创建 Pull Request
