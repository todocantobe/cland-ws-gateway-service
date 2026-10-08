// Package world 共享世界权威服务（B 步）。
//
// 设计：世界服务作为帧网关的一个特殊端点（cid="world"），完全复用现有
// 二进制帧协议（零协议扩展）：
//   - 连接 ws://host:8081/ws?cid=world，加入 room:ops
//   - 世界 tick：Agent 状态机/负载/任务/异常在服务端演化（权威唯一）
//   - 每 1s 向 room:ops 广播状态帧 {kind:"state", agents:[...]}
//   - 接收客户端定向干预帧 {kind:"intervention", action, role?} → 裁决 → 广播
//
// 「世界是所有人共有的，场景也是，只有控制台不是」（02-world §3.1）：
// 所有客户端连到同一个 World，看到同一个现场；控制台视图留在客户端。
package world

import (
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// ─── 世界状态 ─────────────────────────────────────────────────────────

// AgentState 与前端 GameState 六状态一致（idle/running/pending/blocked/error/down）
type Agent struct {
	ID             string  `json:"id"`
	Name           string  `json:"name"`
	Role           string  `json:"role"`
	State          string  `json:"state"`
	Load           int     `json:"load"`
	TasksCompleted int     `json:"tasks"`
	ErrorCount     int     `json:"err"`
	X              float64 `json:"x"`
	Z              float64 `json:"z"`

	// 服务端内部
	speed        float64 // 处理速率倍率（SK-SPEED）
	errorSince   time.Time
	nextTaskIn   time.Duration
	taskProgress float64
}

type stateFrame struct {
	Kind   string  `json:"kind"`
	Agents []Agent `json:"agents"`
}

type eventFrame struct {
	Kind     string `json:"kind"`
	Text     string `json:"text"`
	Severity int    `json:"severity"`
}

type chatFrame struct {
	Kind string `json:"kind"`
	ID   string `json:"id"`
	From string `json:"from"`
	Role string `json:"role"`
	Text string `json:"text"`
}

type interventionFrame struct {
	Kind   string `json:"kind"`
	Action string `json:"action"`
	Role   string `json:"role,omitempty"`
}

// 角色布局与前端 WorldScaler 一致（X 间距 5，调度置后）
var roleOrder = []string{"perception", "reasoning", "execution", "verification", "coordination"}

var namePools = map[string][]string{
	"perception":   {"Echo", "Nova", "Pulse", "Vega", "Flux", "Luma"},
	"reasoning":    {"Cortex", "Muse", "Logic", "Sage", "Astra", "Nexus"},
	"execution":    {"Forge", "Rig", "Talon", "Hack", "Bolt", "Grit"},
	"verification": {"Check", "Verify", "Purity", "Sigma", "Claro", "Valid"},
	"coordination": {"Link", "Mesh", "Route", "Relay", "Harmony", "Bridge"},
}

// 角色间对话语料（与前端 AgentSpeech chatTo 同源语义：流水线相邻角色）
var chatLines = map[string]map[string][]string{
	"perception": {
		"reasoning": {"新任务来了，交给你拆解。", "数据包已解析，转推理层处理。", "收到了一个复杂请求，你分析一下。"},
	},
	"reasoning": {
		"execution": {"方案已制定，执行层请接手。", "任务拆解完成，这是执行步骤。", "按这个计划执行，有问题反馈。"},
	},
	"execution": {
		"verification": {"任务执行完毕，请校验。", "完成了，检查一下结果。", "这是我的输出，审核吧。"},
	},
	"verification": {
		"perception": {"校验完成，可接收新任务。", "结果已确认，反馈给感知层。", "闭环完成，一切正常。"},
	},
}

var pipelineOrder = []string{"perception", "reasoning", "execution", "verification"}

// ─── WorldServer ──────────────────────────────────────────────────────

type WorldServer struct {
	wsURL    string
	cid      string
	room     string
	conn     *websocket.Conn
	writeMu  sync.Mutex
	agents   []*Agent
	elapsed  float64
	totalT   int
	seq      int
	mu       sync.RWMutex
	stop     chan struct{}
	speedEnd time.Time // SK-SPEED 截止
	chatAt   time.Time // 上次对话时刻
}

func New(wsURL, cid, room string) *WorldServer {
	if cid == "" {
		cid = "world"
	}
	if room == "" {
		room = "room:ops"
	}
	return &WorldServer{
		wsURL: wsURL,
		cid:   cid,
		room:  room,
		stop:  make(chan struct{}),
	}
}

// Start 连接网关并启动世界（阻塞；内部自动重连）
func (w *WorldServer) Start() {
	for {
		select {
		case <-w.stop:
			return
		default:
		}
		if err := w.connectAndServe(); err != nil {
			log.Printf("[world] serve error: %v (reconnect in 2s)", err)
			select {
			case <-w.stop:
				return
			case <-time.After(2 * time.Second):
			}
		}
	}
}

func (w *WorldServer) Stop() { close(w.stop) }

func (w *WorldServer) connectAndServe() error {
	conn, _, err := websocket.DefaultDialer.Dial(w.wsURL+"?cid="+w.cid, nil)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	w.conn = conn
	log.Printf("[world] connected to %s as %s", w.wsURL, w.cid)

	w.resetWorld()
	if err := w.joinRoom(); err != nil {
		conn.Close()
		return err
	}

	// 世界 tick：500ms 模拟 + 1s 广播
	tick := time.NewTicker(500 * time.Millisecond)
	defer tick.Stop()
	done := make(chan struct{})
	go func() {
		defer close(done)
		for {
			_, data, err := conn.ReadMessage()
			if err != nil {
				log.Printf("[world] read: %v", err)
				return
			}
			w.handleFrame(data)
		}
	}()

	for {
		select {
		case <-w.stop:
			conn.Close()
			<-done
			return nil
		case <-tick.C:
			w.simulate()
			if int(w.elapsed*1000)%1000 < 500 {
				w.broadcastState()
			}
			// 权威世界的社交：每 ~8s 一对流水线相邻角色对话
			if time.Since(w.chatAt) > 8*time.Second {
				w.chatAt = time.Now()
				w.broadcastChat()
			}
		}
	}
}

// ─── 世界初始化与模拟 ─────────────────────────────────────────────────

func (w *WorldServer) resetWorld() {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.agents = nil
	w.elapsed = 0
	w.totalT = 0
	used := map[string]bool{}
	rand.Shuffle(len(roleOrder), func(i, j int) { roleOrder[i], roleOrder[j] = roleOrder[j], roleOrder[i] })
	for i, role := range roleOrder {
		pool := namePools[role]
		name := pool[rand.Intn(len(pool))]
		for used[name] {
			name = pool[rand.Intn(len(pool))]
		}
		used[name] = true
		x := -10.0 + float64(i)*5.0
		z := 0.0
		if role == "coordination" {
			x, z = 0.0, -5.0
		}
		w.agents = append(w.agents, &Agent{
			ID: fmt.Sprintf("w_%d", i+1), Name: name, Role: role,
			State: "idle", X: x, Z: z, speed: 1,
			nextTaskIn: time.Duration(3+rand.Intn(4)) * time.Second,
		})
	}
	log.Printf("[world] world reset: %d agents", len(w.agents))
}

func (w *WorldServer) simulate() {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.elapsed += 0.5
	if w.elapsed > 190 {
		w.elapsed = 190 // 风暴阶段常驻
	}

	for _, a := range w.agents {
		switch a.State {
		case "error":
			// 自动恢复：error 3s → idle（与前端一致）
			if time.Since(a.errorSince) > 3*time.Second {
				a.State = "idle"
				a.Load = max(0, a.Load-20)
				w.broadcastEvent(fmt.Sprintf("✅ %s 已恢复运行", a.Name), 0)
			}
		case "running":
			// 处理任务：进度推进 → 完成
			rate := 0.09 * a.speed
			if time.Now().Before(w.speedEnd) {
				rate *= 1.5
			}
			a.taskProgress += rate
			if a.taskProgress >= 1 {
				a.taskProgress = 0
				a.TasksCompleted++
				w.totalT++
				a.Load = max(0, a.Load-25)
				if a.Load < 30 {
					a.State = "idle"
				}
				w.broadcastEvent(fmt.Sprintf("✅ %s 完成任务 #%d", a.Name, a.TasksCompleted), 0)
			} else if a.Load > 85 {
				a.State = "pending"
			}
			// 偶发故障
			if rand.Float64() < 0.004 {
				a.State = "error"
				a.ErrorCount++
				a.errorSince = time.Now()
				w.broadcastEvent(fmt.Sprintf("⚠ %s [%s] → 报错", a.Name, a.Role), 2)
			}
		case "idle":
			// 任务到达 → running
			a.nextTaskIn -= 500 * time.Millisecond
			if a.nextTaskIn <= 0 {
				a.State = "running"
				a.Load = min(100, a.Load+20+rand.Intn(20))
				a.nextTaskIn = time.Duration(2+rand.Intn(5)) * time.Second
			}
		case "pending", "blocked":
			// 积压处理：缓慢消化
			if rand.Float64() < 0.08 {
				a.State = "running"
			} else if rand.Float64() < 0.05 {
				a.State = "error"
				a.ErrorCount++
				a.errorSince = time.Now()
			}
		}
	}
}

// ─── 干预裁决（C 步：操作经服务端裁决）────────────────────────────────

func (w *WorldServer) handleIntervention(iv interventionFrame) {
	w.mu.Lock()
	defer w.mu.Unlock()
	switch iv.Action {
	case "rebalance":
		var overloaded, underloaded []*Agent
		for _, a := range w.agents {
			if a.Load > 60 && a.State != "error" && a.State != "down" {
				overloaded = append(overloaded, a)
			}
			if a.Load < 30 && a.State != "error" && a.State != "down" {
				underloaded = append(underloaded, a)
			}
		}
		for _, src := range overloaded {
			for _, dst := range underloaded {
				if src == dst {
					continue
				}
				share := int(float64(src.Load) * 0.3)
				src.Load = max(0, src.Load-share)
				dst.Load = min(100, dst.Load+share)
				break
			}
		}
		log.Printf("[world] intervention: rebalance")
	case "recover":
		for _, a := range w.agents {
			if a.State == "error" || a.State == "down" {
				a.State = "idle"
				a.Load = max(0, a.Load-30)
				log.Printf("[world] intervention: recover %s", a.Name)
				w.broadcastEvent(fmt.Sprintf("💊 故障恢复: %s 已恢复", a.Name), 0)
				break
			}
		}
	case "speedup":
		w.speedEnd = time.Now().Add(5 * time.Second)
		log.Printf("[world] intervention: speedup 5s")
	case "deploy":
		role := iv.Role
		if role == "" {
			role = roleOrder[rand.Intn(len(roleOrder))]
		}
		pool := namePools[role]
		used := map[string]bool{}
		for _, a := range w.agents {
			used[a.Name] = true
		}
		name := pool[rand.Intn(len(pool))]
		for used[name] {
			name = pool[rand.Intn(len(pool))]
		}
		w.seq++
		w.agents = append(w.agents, &Agent{
			ID: fmt.Sprintf("w_%d", len(w.agents)+1), Name: name, Role: role,
			State: "idle", speed: 1,
			X: float64(rand.Intn(13) - 6), Z: float64(rand.Intn(7) - 3),
			nextTaskIn: time.Duration(2+rand.Intn(4)) * time.Second,
		})
		log.Printf("[world] intervention: deploy %s (%s)", name, role)
	}
}

// ─── 网关帧 ───────────────────────────────────────────────────────────

func (w *WorldServer) joinRoom() error {
	roomB := []byte(w.room)
	frame := make([]byte, 2+len(roomB))
	frame[0] = 0x03 // JOIN
	frame[1] = byte(len(roomB))
	copy(frame[2:], roomB)
	return w.write(frame)
}

func (w *WorldServer) broadcastState() {
	w.mu.RLock()
	agents := make([]Agent, len(w.agents))
	for i, a := range w.agents {
		agents[i] = *a
	}
	w.mu.RUnlock()

	payload, err := json.Marshal(stateFrame{Kind: "state", Agents: agents})
	if err != nil {
		return
	}
	roomB := []byte(w.room)
	frame := make([]byte, 2+len(roomB)+len(payload))
	frame[0] = 0x02 // ROOM broadcast
	frame[1] = byte(len(roomB))
	copy(frame[2:], roomB)
	copy(frame[2+len(roomB):], payload)
	if err := w.write(frame); err != nil {
		log.Printf("[world] broadcast: %v", err)
	}
}

// broadcastChat 权威世界的 Agent 对话：广播到目标角色房间（跨实例可见）
func (w *WorldServer) broadcastChat() {
	w.mu.RLock()
	defer w.mu.RUnlock()
	if len(w.agents) < 2 {
		return
	}
	fromRole := pipelineOrder[rand.Intn(len(pipelineOrder))]
	toRole := pipelineOrder[(indexOf(pipelineOrder, fromRole)+1)%len(pipelineOrder)]
	lines := chatLines[fromRole][toRole]
	if len(lines) == 0 {
		return
	}
	var from *Agent
	for _, a := range w.agents {
		if a.Role == fromRole && a.State != "down" {
			from = a
			break
		}
	}
	if from == nil {
		return
	}
	payload, err := json.Marshal(chatFrame{
		Kind: "chat", ID: from.ID, From: from.Name, Role: fromRole,
		Text: lines[rand.Intn(len(lines))],
	})
	if err != nil {
		return
	}
	roomB := []byte("room:" + toRole)
	frame := make([]byte, 2+len(roomB)+len(payload))
	frame[0] = 0x02
	frame[1] = byte(len(roomB))
	copy(frame[2:], roomB)
	copy(frame[2+len(roomB):], payload)
	if err := w.write(frame); err != nil {
		log.Printf("[world] chat: %v", err)
	}
}

func indexOf(list []string, v string) int {
	for i, x := range list {
		if x == v {
			return i
		}
	}
	return 0
}

func (w *WorldServer) broadcastEvent(text string, severity int) {
	payload, err := json.Marshal(eventFrame{Kind: "event", Text: text, Severity: severity})
	if err != nil {
		return
	}
	roomB := []byte(w.room)
	frame := make([]byte, 2+len(roomB)+len(payload))
	frame[0] = 0x02
	frame[1] = byte(len(roomB))
	copy(frame[2:], roomB)
	copy(frame[2+len(roomB):], payload)
	if err := w.write(frame); err != nil {
		log.Printf("[world] event: %v", err)
	}
}

// handleFrame 读取循环：只处理定向帧（客户端 → world 的干预）
func (w *WorldServer) handleFrame(data []byte) {
	if len(data) < 3 {
		return
	}
	if data[0] != 0x01 { // 仅 DIRECT
		return
	}
	srcLen := int(data[1])
	if len(data) < 2+srcLen {
		return
	}
	payload := data[2+srcLen:]
	var iv interventionFrame
	if err := json.Unmarshal(payload, &iv); err != nil || iv.Kind != "intervention" {
		return
	}
	w.handleIntervention(iv)
	// 干预后立即广播一帧，反馈更快
	w.broadcastState()
}

func (w *WorldServer) write(frame []byte) error {
	w.writeMu.Lock()
	defer w.writeMu.Unlock()
	if w.conn == nil {
		return fmt.Errorf("no conn")
	}
	return w.conn.WriteMessage(websocket.BinaryMessage, frame)
}
