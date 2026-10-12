package main

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"cland.org/cland-chat-service/core/infrastructure/delivery/websocket/gateway"

	"cland.org/cland-chat-service/core/application"
	"go.uber.org/zap"

	"cland.org/cland-chat-service/core/infrastructure/config"
	cland_http "cland.org/cland-chat-service/core/infrastructure/delivery/http"
	"cland.org/cland-chat-service/core/infrastructure/logger"
	"cland.org/cland-chat-service/core/infrastructure/repository"
)

func main() {
	// Load configuration
	cfg, err := config.Load()
	if err != nil {
		zap.L().Fatal("Failed to load config", zap.Error(err))
	}

	// Initialize logger
	logger.InitConfig(cfg.Log)
	zapLogger := logger.GetLogger()

	// Validate configuration
	if cfg.Server.Port == 0 {
		zapLogger.Fatal("Invalid server port configuration")
	}

	// Initialize repositories（极简 WS 网关：内存仓储，无持久化）
	messageRepo := repository.NewMemoryMessageRepository()
	sessionRepo := repository.NewMemorySessionRepository()
	userRepo := repository.NewMemoryUserRepository()

	// Initialize use cases
	chatUseCase := application.NewChatService(
		messageRepo, // messageRepo
		sessionRepo, // sessionRepo
		userRepo,    // userRepo
	)

	// Initialize HTTP router
	httpRouter := cland_http.GetRouter(chatUseCase)
	httpRouter.Use(logger.GinRecovery(zapLogger, true))
	httpRouter.Use(logger.GinLogger(zapLogger))

	// Initialize WebSocket gateway（原生二进制帧转发，逻辑层自定义）
	go gateway.InitWsGateway(zapLogger)

	// Create HTTP server
	httpServer := &http.Server{
		Addr:    fmt.Sprintf(":%d", cfg.Server.Port),
		Handler: httpRouter,
	}

	var wg sync.WaitGroup
	wg.Add(1)

	// Start HTTP server (which now includes WebSocket)
	go func() {
		defer wg.Done()
		zapLogger.Info("Starting HTTP server",
			zap.String("address", httpServer.Addr))
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			zapLogger.Fatal("Failed to start HTTP server", zap.Error(err))
		}
	}()

	// Graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM, syscall.SIGQUIT)

	// Create main context for the application
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	select {
	case sig := <-sigChan:
		zapLogger.Info("Received shutdown signal", zap.String("signal", sig.String()))
	case <-ctx.Done():
		zapLogger.Info("Context cancelled, shutting down")
	}

	zapLogger.Info("Shutting down server gracefully...")

	// First try graceful shutdown
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer shutdownCancel()

	var shutdownErr error
	if err := httpServer.Shutdown(shutdownCtx); err != nil {
		zapLogger.Error("Failed to shutdown server gracefully", zap.Error(err))
		shutdownErr = err
	}

	wg.Wait()

	if shutdownErr != nil {
		zapLogger.Fatal("Failed to shutdown server gracefully")
	}
	zapLogger.Info("Server stopped gracefully")
}
