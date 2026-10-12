package connection

import (
	"fmt"
	"sync"

	"github.com/gorilla/websocket"
	"go.uber.org/zap"
)

// Manager 连接注册表：cid -> 连接 + 房间成员表。
// 所有写操作经 WriteTo（每连接写锁串行化，gorilla 要求单 writer）。
type Manager struct {
	connections map[string]*websocket.Conn // cid -> connection
	connLocks   map[string]*sync.Mutex     // cid -> 写锁（串行化 WriteMessage）
	rooms       map[string]map[string]bool // roomID -> cid 集合
	mu          sync.RWMutex
	log         *zap.Logger
}

// NewManager 创建连接管理器
func NewManager(log *zap.Logger) *Manager {
	return &Manager{
		connections: make(map[string]*websocket.Conn),
		connLocks:   make(map[string]*sync.Mutex),
		rooms:       make(map[string]map[string]bool),
		log:         log,
	}
}

// AddConnection 添加连接
func (m *Manager) AddConnection(conn *websocket.Conn, cid string) {
	if cid == "" {
		m.log.Error("Empty cid provided")
		return
	}

	m.mu.Lock()
	m.connections[cid] = conn
	m.connLocks[cid] = &sync.Mutex{}
	m.mu.Unlock()

	m.log.Info("New connection", zap.String("cid", cid))
}

// RemoveConnection 移除连接（含所有房间成员资格）
func (m *Manager) RemoveConnection(cid string) {
	m.mu.Lock()
	delete(m.connections, cid)
	delete(m.connLocks, cid)
	for roomID, users := range m.rooms {
		if _, ok := users[cid]; ok {
			delete(users, cid)
			if len(users) == 0 {
				delete(m.rooms, roomID)
			}
		}
	}
	m.mu.Unlock()

	m.log.Info("Connection removed", zap.String("cid", cid))
}

// GetConnection 获取用户连接
func (m *Manager) GetConnection(cid string) (*websocket.Conn, bool) {
	m.mu.RLock()
	conn, ok := m.connections[cid]
	m.mu.RUnlock()
	return conn, ok
}

// GetRoomUserIDs 获取房间内所有在线成员 cid
func (m *Manager) GetRoomUserIDs(roomID string) []string {
	m.mu.RLock()
	defer m.mu.RUnlock()

	var cids []string
	if users, exists := m.rooms[roomID]; exists {
		for cid := range users {
			if _, ok := m.connections[cid]; ok {
				cids = append(cids, cid)
			}
		}
	}
	return cids
}

// WriteTo 向指定 cid 写二进制帧（带写锁，并发安全）
func (m *Manager) WriteTo(cid string, data []byte) error {
	m.mu.RLock()
	conn, ok := m.connections[cid]
	lock := m.connLocks[cid]
	m.mu.RUnlock()

	if !ok {
		return fmt.Errorf("cid %s not connected", cid)
	}

	lock.Lock()
	defer lock.Unlock()
	return conn.WriteMessage(websocket.BinaryMessage, data)
}

// JoinRoom 加入房间
func (m *Manager) JoinRoom(cid, roomID string) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if _, exists := m.rooms[roomID]; !exists {
		m.rooms[roomID] = make(map[string]bool)
	}
	m.rooms[roomID][cid] = true
	m.log.Info("User joined room", zap.String("cid", cid), zap.String("roomID", roomID))
}

// LeaveRoom 离开房间
func (m *Manager) LeaveRoom(cid, roomID string) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if users, exists := m.rooms[roomID]; exists {
		delete(users, cid)
		if len(users) == 0 {
			delete(m.rooms, roomID)
		}
		m.log.Info("User left room", zap.String("cid", cid), zap.String("roomID", roomID))
	}
}
