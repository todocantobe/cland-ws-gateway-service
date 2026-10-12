package gateway

import (
	"errors"
	"fmt"
)

// 帧类型：客户端 → 网关（请求帧）
const (
	FrameDirect = 0x01 // [01][dstLen u8][dst][payload]          定向转发
	FrameRoom   = 0x02 // [02][roomLen u8][room][payload]        房间广播（不含发送者）
	FrameJoin   = 0x03 // [03][roomLen u8][room]                 加入房间
	FrameLeave  = 0x04 // [04][roomLen u8][room]                 离开房间
	FramePing   = 0x05 // [05]                                  应用层心跳
)

// 帧类型：网关 → 客户端（投递帧）
const (
	FrameDeliverDirect = 0x01 // [01][srcLen u8][src][payload]   定向投递
	FrameDeliverRoom   = 0x02 // [02][srcLen u8][src][payload]   房间投递
	FrameJoined        = 0x03 // [03][roomLen u8][room]          join 确认
	FrameLeft          = 0x04 // [04][roomLen u8][room]          leave 确认
	FramePong          = 0x05 // [05]                            pong
	FrameError         = 0xFF // [FF][code u8][msgLen u8][msg]   错误
)

// 错误码（FrameError 的 code）
const (
	ErrCodeBadFrame    = 0x01 // 帧头解析失败
	ErrCodeTargetOff   = 0x02 // 定向目标离线
	ErrCodeUnknownType = 0x03 // 未知帧类型
	ErrCodeBadParam    = 0x04 // 参数错误（空 dst/room 等）
)

// 帧解析错误（哨兵，供调用方区分错误码）
var (
	ErrUnknownFrameType = errors.New("unknown frame type")
	ErrBadFrame         = errors.New("bad frame")
)

// RequestFrame 解析后的请求帧。
// Payload 直接引用输入 data 的子切片（零拷贝），仅在同一读循环内有效。
type RequestFrame struct {
	Type    byte
	Target  string // DIRECT 的 dst 或 ROOM/JOIN/LEAVE 的 room
	Payload []byte // 待转发的业务数据（原样转发，网关不解析）
}

// ParseRequestFrame 解析客户端请求帧头：
//
//	DIRECT: [01][dstLen][dst][payload]
//	ROOM:   [02][roomLen][room][payload]
//	JOIN:   [03][roomLen][room]
//	LEAVE:  [04][roomLen][room]
//	PING:   [05]
func ParseRequestFrame(data []byte) (*RequestFrame, error) {
	if len(data) < 1 {
		return nil, errors.New("empty frame")
	}
	f := &RequestFrame{Type: data[0]}
	rest := data[1:]

	switch f.Type {
	case FramePing:
		// 无参数
		if len(rest) != 0 {
			return nil, errors.New("ping frame must have no payload")
		}
		return f, nil
	case FrameDirect, FrameRoom, FrameJoin, FrameLeave:
		if len(rest) < 1 {
			return nil, fmt.Errorf("frame %02x missing target length", f.Type)
		}
		targetLen := int(rest[0])
		if len(rest) < 1+targetLen {
			return nil, fmt.Errorf("frame %02x truncated target", f.Type)
		}
		f.Target = string(rest[1 : 1+targetLen])
		if f.Target == "" {
			return nil, errors.New("empty target")
		}
		f.Payload = rest[1+targetLen:]
		return f, nil
	default:
		return nil, fmt.Errorf("%w: %02x", ErrUnknownFrameType, f.Type)
	}
}

// BuildDeliverFrame 构建投递帧：[type][srcLen][src][payload]
func BuildDeliverFrame(frameType byte, src string, payload []byte) []byte {
	frame := make([]byte, 0, 2+len(src)+len(payload))
	frame = append(frame, frameType, byte(len(src)))
	frame = append(frame, src...)
	frame = append(frame, payload...)
	return frame
}

// BuildTargetAckFrame 构建 join/leave 确认帧：[type][roomLen][room]
func BuildTargetAckFrame(frameType byte, target string) []byte {
	frame := make([]byte, 0, 2+len(target))
	frame = append(frame, frameType, byte(len(target)))
	frame = append(frame, target...)
	return frame
}

// BuildPongFrame 构建 pong 帧：[05]
func BuildPongFrame() []byte {
	return []byte{FramePong}
}

// BuildErrorFrame 构建错误帧：[FF][code][msgLen][msg]
func BuildErrorFrame(code byte, msg string) []byte {
	frame := make([]byte, 0, 3+len(msg))
	frame = append(frame, FrameError, code, byte(len(msg)))
	frame = append(frame, msg...)
	return frame
}
