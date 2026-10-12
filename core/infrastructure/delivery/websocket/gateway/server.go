package gateway

import (
	"errors"
	"net/http"
	"sync"
	"time"

	"cland.org/cland-chat-service/core/infrastructure/delivery/websocket/connection"
	"github.com/gorilla/websocket"
	"go.uber.org/zap"
)

// WsGateway 原生 WebSocket 帧转发网关：
//   - 连接：ws://host:8081/ws?cid=user_001（cid 必填，缺失 HTTP 401）
//   - 帧：二进制，网关只解析 1 字节类型 + 路由目标，payload 原样转发（逻辑层自定义）
//   - 心跳：websocket 协议级控制帧 ping/pong（零业务字节开销）
type WsGateway struct {
	logger      *zap.Logger
	upgrader    websocket.Upgrader
	connManager *connection.Manager
	once        sync.Once

	pingInterval time.Duration // 服务端控制帧 ping 周期
	readTimeout  time.Duration // 读超时（超过即判定离线）
}

// NewWsGateway 创建帧转发网关
func NewWsGateway(logger *zap.Logger) *WsGateway {
	return &WsGateway{
		logger: logger,
		upgrader: websocket.Upgrader{
			ReadBufferSize:  4096,
			WriteBufferSize: 4096,
			CheckOrigin: func(r *http.Request) bool {
				return true // 游戏客户端全源放行
			},
		},
		pingInterval: 25 * time.Second,
		readTimeout:  45 * time.Second, // pingInterval + 20s 余量
	}
}

// InitWsGateway 初始化并启动网关（阻塞监听 8081）
func InitWsGateway(logger *zap.Logger) *WsGateway {
	g := NewWsGateway(logger)
	g.once.Do(func() {
		g.setup()
	})
	return g
}

// setup 注册路由并监听
func (g *WsGateway) setup() {
	log := g.logger.Named("ws-gateway")
	g.connManager = connection.NewManager(g.logger)

	http.HandleFunc("/ws", g.handleWS)

	log.Info("WS gateway serving at :8081/ws")
	if err := http.ListenAndServe(":8081", nil); err != nil {
		log.Error("ws gateway ListenAndServe", zap.Error(err))
	}
}

// handleWS 原生 WebSocket 连接处理
func (g *WsGateway) handleWS(w http.ResponseWriter, r *http.Request) {
	log := g.logger.With(zap.String("remote_addr", r.RemoteAddr))

	// 认证：cid 必填
	cid := r.URL.Query().Get("cid")
	if cid == "" {
		log.Warn("Missing cid, rejecting connection")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"code":40010010001,"message":"missing cid"}`))
		return
	}

	conn, err := g.upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Error("Failed to upgrade connection", zap.Error(err))
		return
	}

	// 注册连接（唯一注册表）
	g.connManager.AddConnection(conn, cid)
	g.serve(conn, cid)
}

// serve 连接事件循环：心跳写 goroutine + 读循环（读一帧 → 查表 → 转写，O(1) 直转）
func (g *WsGateway) serve(conn *websocket.Conn, cid string) {
	log := g.logger.With(zap.String("cid", cid), zap.String("remote_addr", conn.RemoteAddr().String()))

	// 心跳：服务端每 pingInterval 发协议级控制帧 ping。
	// gorilla 保证 WriteControl 可与其他方法并发，无需写锁。
	done := make(chan struct{})
	defer close(done)
	go func() {
		ticker := time.NewTicker(g.pingInterval)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				if err := conn.WriteControl(websocket.PingMessage, nil, time.Now().Add(5*time.Second)); err != nil {
					return
				}
			case <-done:
				return
			}
		}
	}()

	// 读循环
	for {
		conn.SetReadDeadline(time.Now().Add(g.readTimeout))
		_, data, err := conn.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err, websocket.CloseGoingAway, websocket.CloseAbnormalClosure) {
				log.Error("WebSocket read error", zap.Error(err))
			}
			g.connManager.RemoveConnection(cid)
			_ = conn.Close()
			return
		}

		if err := g.handleFrame(conn, cid, data); err != nil {
			log.Warn("Frame handling error", zap.Error(err))
		}
	}
}

// handleFrame 帧路由：解析 1 字节类型 + 目标，payload 原样转发（零业务解析）
func (g *WsGateway) handleFrame(conn *websocket.Conn, cid string, data []byte) error {
	frame, err := ParseRequestFrame(data)
	if err != nil {
		code := byte(ErrCodeBadFrame)
		if errors.Is(err, ErrUnknownFrameType) {
			code = ErrCodeUnknownType
		}
		return g.connManager.WriteTo(cid, BuildErrorFrame(code, err.Error()))
	}

	switch frame.Type {
	case FrameDirect:
		// 定向转发：目标在线则投递 [01][src][payload]，离线回错误帧
		if _, ok := g.connManager.GetConnection(frame.Target); !ok {
			return g.connManager.WriteTo(cid, BuildErrorFrame(ErrCodeTargetOff, "target offline: "+frame.Target))
		}
		return g.connManager.WriteTo(frame.Target, BuildDeliverFrame(FrameDeliverDirect, cid, frame.Payload))

	case FrameRoom:
		// 房间广播：投递给房间内除发送者外的所有在线成员（房间空则静默）
		for _, member := range g.connManager.GetRoomUserIDs(frame.Target) {
			if member == cid {
				continue
			}
			if err := g.connManager.WriteTo(member, BuildDeliverFrame(FrameDeliverRoom, cid, frame.Payload)); err != nil {
				g.logger.Warn("Room delivery failed",
					zap.String("room", frame.Target), zap.String("cid", cid), zap.Error(err))
			}
		}
		return nil

	case FrameJoin:
		g.connManager.JoinRoom(cid, frame.Target)
		return g.connManager.WriteTo(cid, BuildTargetAckFrame(FrameJoined, frame.Target))

	case FrameLeave:
		g.connManager.LeaveRoom(cid, frame.Target)
		return g.connManager.WriteTo(cid, BuildTargetAckFrame(FrameLeft, frame.Target))

	case FramePing:
		return g.connManager.WriteTo(cid, BuildPongFrame())

	default:
		return g.connManager.WriteTo(cid, BuildErrorFrame(ErrCodeUnknownType, "unknown frame type"))
	}
}
