package input

import (
	"context"

	"cland.org/cland-chat-service/core/application"
	"cland.org/cland-chat-service/core/domain/entity"
)

// ChatController 聊天控制器接口
type ChatController interface {
	// SendMessage 发送消息
	SendMessage(ctx context.Context, message *entity.Message) error

	// GetSessionMessages 获取会话消息
	GetSessionMessages(ctx context.Context, sessionID string) ([]*entity.Message, error)

	// CreateSession 创建会话
	CreateSession(ctx context.Context, userID string) (*entity.Session, error)

	// CloseSession 关闭会话
	CloseSession(ctx context.Context, sessionID string) error

	// GetOfflineMessages 获取离线消息
	GetOfflineMessages(ctx context.Context, userID string) ([]*entity.Message, error)

	// ProcessMessageStatus 处理消息状态更新
	ProcessMessageStatus(ctx context.Context, msgID string, newStatus uint8) error
}

// HTTPChatController HTTP聊天控制器实现
type HTTPChatController struct {
	chatService *application.ChatService
}

// NewHTTPChatController 创建HTTP聊天控制器
func NewHTTPChatController(chatService *application.ChatService) *HTTPChatController {
	return &HTTPChatController{
		chatService: chatService,
	}
}

// SendMessage 发送消息
func (c *HTTPChatController) SendMessage(ctx context.Context, message *entity.Message) error {
	return c.chatService.SendMessage(ctx, message)
}

// GetSessionMessages 获取会话消息
func (c *HTTPChatController) GetSessionMessages(ctx context.Context, sessionID string) ([]*entity.Message, error) {
	return c.chatService.GetSessionMessages(ctx, sessionID)
}

// CreateSession 创建会话
func (c *HTTPChatController) CreateSession(ctx context.Context, userID string) (*entity.Session, error) {
	return c.chatService.CreateSession(ctx, userID)
}

// CloseSession 关闭会话
func (c *HTTPChatController) CloseSession(ctx context.Context, sessionID string) error {
	return c.chatService.CloseSession(ctx, sessionID)
}

// GetOfflineMessages 获取离线消息
func (c *HTTPChatController) GetOfflineMessages(ctx context.Context, userID string) ([]*entity.Message, error) {
	return c.chatService.GetOfflineMessages(ctx, userID)
}

// ProcessMessageStatus 处理消息状态更新
func (c *HTTPChatController) ProcessMessageStatus(ctx context.Context, msgID string, newStatus uint8) error {
	return c.chatService.ProcessMessageStatus(ctx, msgID, newStatus)
}
