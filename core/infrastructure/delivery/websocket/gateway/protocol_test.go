package gateway

import (
	"bytes"
	"testing"
)

func TestParseRequestFrame(t *testing.T) {
	cases := []struct {
		name        string
		data        []byte
		wantType    byte
		wantTarget  string
		wantPayload []byte
		wantErr     bool
	}{
		{
			name:        "direct",
			data:        append([]byte{FrameDirect, 8}, append([]byte("user_002"), []byte("hello")...)...),
			wantType:    FrameDirect,
			wantTarget:  "user_002",
			wantPayload: []byte("hello"),
		},
		{
			name:        "direct empty payload",
			data:        []byte{FrameDirect, 4, 'u', 's', 'e', 'r'},
			wantType:    FrameDirect,
			wantTarget:  "user",
			wantPayload: nil,
		},
		{
			name:        "room",
			data:        append([]byte{FrameRoom, 5}, append([]byte("roomA"), 1, 2, 3)...),
			wantType:    FrameRoom,
			wantTarget:  "roomA",
			wantPayload: []byte{1, 2, 3},
		},
		{
			name:       "join",
			data:       []byte{FrameJoin, 5, 'r', 'o', 'o', 'm', 'A'},
			wantType:   FrameJoin,
			wantTarget: "roomA",
		},
		{
			name:       "leave",
			data:       []byte{FrameLeave, 1, 'x'},
			wantType:   FrameLeave,
			wantTarget: "x",
		},
		{
			name:     "ping",
			data:     []byte{FramePing},
			wantType: FramePing,
		},
		{name: "empty frame", data: []byte{}, wantErr: true},
		{name: "unknown type", data: []byte{0x99, 1, 'a'}, wantErr: true},
		{name: "missing len", data: []byte{FrameDirect}, wantErr: true},
		{name: "truncated target", data: []byte{FrameDirect, 10, 'a'}, wantErr: true},
		{name: "empty target", data: []byte{FrameDirect, 0}, wantErr: true},
		{name: "ping with payload", data: []byte{FramePing, 1}, wantErr: true},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f, err := ParseRequestFrame(tc.data)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("expected error, got frame %+v", f)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if f.Type != tc.wantType {
				t.Errorf("type = %02x, want %02x", f.Type, tc.wantType)
			}
			if f.Target != tc.wantTarget {
				t.Errorf("target = %q, want %q", f.Target, tc.wantTarget)
			}
			if !bytes.Equal(f.Payload, tc.wantPayload) {
				t.Errorf("payload = %v, want %v", f.Payload, tc.wantPayload)
			}
		})
	}
}

// TestParseRequestFrame_ZeroCopy 验证 payload 引用输入切片（零拷贝转发前提）
func TestParseRequestFrame_ZeroCopy(t *testing.T) {
	data := []byte{FrameDirect, 3, 'a', 'b', 'c', 0xDE, 0xAD}
	f, err := ParseRequestFrame(data)
	if err != nil {
		t.Fatal(err)
	}
	if len(f.Payload) != 2 || f.Payload[0] != 0xDE {
		t.Fatalf("payload = %v", f.Payload)
	}
	data[5] = 0x00 // 修改原切片
	if f.Payload[0] != 0x00 {
		t.Error("payload 未引用原切片（发生拷贝）")
	}
}

func TestBuildFrames(t *testing.T) {
	if got := BuildDeliverFrame(FrameDeliverDirect, "u1", []byte{1, 2}); !bytes.Equal(got, []byte{FrameDeliverDirect, 2, 'u', '1', 1, 2}) {
		t.Errorf("deliver = %v", got)
	}
	if got := BuildTargetAckFrame(FrameJoined, "roomA"); !bytes.Equal(got, []byte{FrameJoined, 5, 'r', 'o', 'o', 'm', 'A'}) {
		t.Errorf("ack = %v", got)
	}
	if got := BuildPongFrame(); !bytes.Equal(got, []byte{FramePong}) {
		t.Errorf("pong = %v", got)
	}
	wantErr := []byte{FrameError, ErrCodeTargetOff, 5, 'o', 'f', 'f', 'l', 'i'}
	if got := BuildErrorFrame(ErrCodeTargetOff, "offli"); !bytes.Equal(got, wantErr) {
		t.Errorf("err = %v, want %v", got, wantErr)
	}
}
