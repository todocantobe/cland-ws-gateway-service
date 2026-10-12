package handler

import (
	"net/http"

	"cland.org/cland-chat-service/common/constants"
	"cland.org/cland-chat-service/core/application"
	"cland.org/cland-chat-service/core/domain/repository"
	"cland.org/cland-chat-service/core/infrastructure/delivery/http/response"
	"github.com/gin-gonic/gin"
)

// UserResponse represents the response structure for user operations
type UserResponse struct {
	SessionID    string `json:"sessionId"`
	SubSessionID string `json:"subSessionId"`
	Token        string `json:"token"`
}

type UserHandler struct {
	userRepo    repository.UserRepository
	sessionRepo repository.SessionRepository
	userService *application.UserService
}

func NewUserHandler(
	userRepo repository.UserRepository,
	sessionRepo repository.SessionRepository,
	userService *application.UserService,
) *UserHandler {
	return &UserHandler{
		userRepo:    userRepo,
		sessionRepo: sessionRepo,
		userService: userService,
	}
}

// InitUser initializes a new user session
// @Summary Initialize user session
// @Description Creates a new user session and returns authentication tokens
// @Tags user
// @Accept json
// @Produce json
// @Success 200 {object} UserResponse
// @Failure 500 {object} ErrorResponse
// @Router /api/init [post]
func (h *UserHandler) InitUser(c *gin.Context) {
	ctx := c.Request.Context()

	// Check for existing cland-cid in headers or cookie
	var existingCID string
	if cid := c.GetHeader(constants.KEY_USER_ID); cid != "" {
		existingCID = cid
	}

	// Call application service
	res, err := h.userService.InitUser(ctx, existingCID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, response.Response{
			Code: constants.ErrorCodeUserInitFailed,
			Msg:  "Failed to initialize user",
			Data: gin.H{
				"error_field":  "user",
				"error_detail": err.Error(),
			},
		})
		return
	}

	// Set cland-cid cookie
	c.SetCookie(
		constants.KEY_USER_ID,
		res.ClandCID,
		constants.CookieMaxAgeOneYear,
		constants.CookiePathRoot,
		"", // TODO: Set domain from config
		constants.CookieSecure,
		constants.CookieHttpOnly,
	)

	// Return response matching spec
	c.JSON(http.StatusOK, response.Response{
		Code: constants.SuccessCode,
		Msg:  "Initialization successful",
		Data: gin.H{
			"sessionId":    res.SessionID,
			"subSessionId": res.SubSessionID,
			"token":        res.Token,
		},
	})
}
