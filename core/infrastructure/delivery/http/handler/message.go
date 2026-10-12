package handler

import (
	"net/http"
	"time"

	"cland.org/cland-chat-service/core/application"
	"cland.org/cland-chat-service/core/domain/entity"
	"cland.org/cland-chat-service/core/infrastructure/delivery/http/response"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

// Message represents a chat message
type Message struct {
	MsgID       string                 `json:"msgId"`
	SessionID   string                 `json:"sessionId"`
	Content     string                 `json:"content"`
	Src         string                 `json:"src"`
	Dst         string                 `json:"dst"`
	MsgType     entity.MsgType         `json:"msgType"`
	ContentType entity.ContentType     `json:"contentType"`
	Status      entity.Status          `json:"status"`
	Ts          string                 `json:"ts"`
	Ext         map[string]interface{} `json:"ext"`
}

// MessageResponse represents the response structure for message operations
type MessageResponse struct {
	Status string      `json:"status"`
	Data   interface{} `json:"data"`
}

type MessageHandler struct {
	chatService *application.ChatService
}

func NewMessageHandler(chatService *application.ChatService) *MessageHandler {
	return &MessageHandler{chatService: chatService}
}

// GetOfflineMessages retrieves offline messages for a user
// @Summary Get offline messages
// @Description Retrieves all offline messages for the specified user
// @Tags messages
// @Accept json
// @Produce json
// @Param userId query string true "User ID"
// @Success 200 {object} MessageResponse
// @Failure 400 {object} ErrorResponse
// @Failure 500 {object} ErrorResponse
// @Router /api/messages/offline [get]
func (h *MessageHandler) GetOfflineMessages(c *gin.Context) {
	userID := c.Query("userId")
	if userID == "" {
		c.JSON(http.StatusBadRequest, response.Response{
			Code: http.StatusBadRequest,
			Msg:  "userId is required",
		})
		return
	}

	ctx := c.Request.Context()

	messages, err := h.chatService.GetOfflineMessages(ctx, userID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, response.Response{
			Code: http.StatusInternalServerError,
			Msg:  "failed to get offline messages",
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"status": "ok",
		"data": gin.H{
			"messages": messages,
		},
	})
}

// SendChatMessage sends a new chat message
// @Summary Send chat message
// @Description Sends a new chat message
// @Tags messages
// @Accept json
// @Produce json
// @Param message body handler.Message true "Message to send"
// @Success 200 {object} MessageResponse
// @Failure 400 {object} ErrorResponse
// @Failure 500 {object} ErrorResponse
// @Router /api/messages [post]
func (h *MessageHandler) SendChatMessage(c *gin.Context) {
	var req struct {
		SessionID string `json:"sessionId"`
		Content   string `json:"content"`
		SenderID  string `json:"senderId"`
	}

	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, response.Response{
			Code: http.StatusBadRequest,
			Msg:  "invalid request",
		})
		return
	}

	ctx := c.Request.Context()
	// Generate proper IDs
	msgID := "m" + uuid.New().String()
	subSessionID := "ss" + uuid.New().String()

	message := &entity.Message{
		MsgID:       msgID,
		SessionID:   req.SessionID,
		Content:     req.Content,
		Src:         "U:" + req.SenderID,
		Dst:         "S:auto", // Default to bot
		MsgType:     entity.MsgTypeMessage,
		ContentType: entity.ContentTypeText,
		Status:      entity.StatusNew,
		Ts:          entity.StringTimestamp(time.Now().UnixNano() / int64(time.Millisecond)),
		Ext: map[string]interface{}{
			"subSessionId": subSessionID,
		},
	}

	if err := h.chatService.SendMessage(ctx, message); err != nil {
		c.JSON(http.StatusInternalServerError, response.Response{
			Code: http.StatusInternalServerError,
			Msg:  "failed to send message",
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"status": "ok",
		"data": gin.H{
			"msgId":        message.MsgID,
			"sessionId":    message.SessionID,
			"subSessionId": message.SubSessionID,
			"content":      message.Content,
			"src":          message.Src,
			"ts":           message.Ts,
		},
	})
}
