package router

import (
	"net/http"
	"sync"

	"cland.org/cland-chat-service/core/infrastructure/delivery/http/handler"
	"cland.org/cland-chat-service/core/usecase"
	_ "cland.org/cland-chat-service/docs/swagger"
	"github.com/gin-gonic/gin"
	swaggerFiles "github.com/swaggo/files"
	ginSwagger "github.com/swaggo/gin-swagger"
)

var (
	once   sync.Once
	router *gin.Engine
)

func GetRouter(chatUseCase *usecase.ChatUseCase) *gin.Engine {
	once.Do(func() {
		router = gin.Default()
		setupRoutes(router, chatUseCase)
	})
	return router
}

func setupRoutes(r *gin.Engine, chatUseCase *usecase.ChatUseCase) {
	// Swagger route
	r.GET("/swagger/*any", ginSwagger.WrapHandler(swaggerFiles.Handler))

	// CORS middleware
	r.Use(func(c *gin.Context) {
		// Set CORS headers for all responses
		c.Header("Access-Control-Allow-Origin", "*")
		c.Header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
		c.Header("Access-Control-Allow-Headers", "Content-Type, Authorization")
		c.Header("Access-Control-Max-Age", "86400")

		// Handle OPTIONS requests
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(http.StatusOK)
			return
		}
		c.Next()
	})

	// 健康检查（标准路径 /health；部署流水线门禁以此为准）
	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})

	// API路由分组
	api := r.Group("/api")
	{
		// User initialization
		userUC := usecase.NewUserUseCase(
			chatUseCase.UserRepo,
			chatUseCase.SessionRepo,
		)
		userHandler := handler.NewUserHandler(
			chatUseCase.UserRepo,
			chatUseCase.SessionRepo,
			userUC,
		)
		api.POST("/init", userHandler.InitUser)

		// 离线消息
		msgHandler := handler.NewMessageHandler(chatUseCase)
		api.GET("/messages/offline", msgHandler.GetOfflineMessages)
	}
}
