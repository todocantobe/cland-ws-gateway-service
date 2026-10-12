// world-server：共享世界权威服务（B 步）——作为帧网关的端点 cid=world 参与世界。
//
// 用法：
//
//	WS_URL=ws://127.0.0.1:8081/ws go run ./cmd/world
package main

import (
	"flag"
	"os"
	"os/signal"
	"syscall"

	"cland.org/cland-chat-service/world"
)

func main() {
	wsURL := flag.String("ws", envOr("WS_URL", "ws://127.0.0.1:8081/ws"), "gateway ws url")
	flag.Parse()

	server := world.New(*wsURL, "world", "room:ops")
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sig
		server.Stop()
		os.Exit(0)
	}()
	server.Start()
}

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
