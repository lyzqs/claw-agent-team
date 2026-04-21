package middleware

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
)

// upstreamBridgeURL is the OTLP bridge upstream endpoint.
// Override via InitUpstreamBridgeMiddleware.
var upstreamBridgeURL = "http://127.0.0.1:19160/v1/upstream"

// channelCache stores channel names by ID, refreshed every 5 minutes.
var (
	channelCache   = make(map[int]string)
	channelCacheMu sync.RWMutex
	lastCacheLoad  time.Time
	cacheWindow    = 5 * time.Minute
)

// InitUpstreamBridgeMiddleware sets the bridge URL.
// Call once during NewAPI startup.
func InitUpstreamBridgeMiddleware(bridgeURL string) {
	if bridgeURL != "" {
		upstreamBridgeURL = bridgeURL
	}
}

// upstreamEvent is the POST payload for the bridge /v1/upstream endpoint.
type upstreamEvent struct {
	Channel      string `json:"channel"`
	HTTPStatus   int    `json:"http_status_code"`
	DurationMs   int64  `json:"duration_ms"`
	TokensTotal  int64  `json:"tokens_total"`
	TokensInput  int64  `json:"tokens_input"`
	TokensOutput int64  `json:"tokens_output"`
	StatusFamily string `json:"status_family"`
	IsError      bool   `json:"is_error"`
}

// usagePayload mirrors the OpenAI-compatible usage field in responses.
type usagePayload struct {
	PromptTokens     int64 `json:"prompt_tokens,omitempty"`
	CompletionTokens int64 `json:"completion_tokens,omitempty"`
	TotalTokens      int64 `json:"total_tokens,omitempty"`
}

// openAIRespPayload is the outer response wrapper NewAPI returns.
type openAIRespPayload struct {
	Usage *usagePayload `json:"usage,omitempty"`
}

// resolveChannelName resolves the human-readable channel name from Gin context.
// Tries first-class context key, falls back to DB lookup with in-process cache.
func resolveChannelName(c *gin.Context) string {
	// Try first-class channel_name context key (set by distributor middleware).
	if name := common.GetContextKeyString(c, constant.ContextKeyChannelName); name != "" {
		return name
	}
	// Try channel_id context key.
	if channelId := common.GetContextKeyInt(c, constant.ContextKeyChannelId); channelId > 0 {
		// Check cache first.
		channelCacheMu.RLock()
		name, ok := channelCache[channelId]
		withinTTL := time.Since(lastCacheLoad) < cacheWindow
		channelCacheMu.RUnlock()
		if ok && withinTTL {
			return name
		}
		// Cache miss: query DB for this channel.
		if ch, err := model.GetChannelById(channelId, false); err == nil && ch != nil {
			name := ch.Name
			if name == "" {
				name = fmt.Sprintf("channel-%d", channelId)
			}
			channelCacheMu.Lock()
			if time.Since(lastCacheLoad) >= cacheWindow {
				// Refresh all channels into cache.
				lastCacheLoad = time.Now()
				if channels, err := model.GetAllChannels(0, 0, false, false); err == nil {
					clear(channelCache)
					for _, ch := range channels {
						cname := ch.Name
						if cname == "" {
							cname = fmt.Sprintf("channel-%d", ch.Id)
						}
						channelCache[ch.Id] = cname
					}
				}
			} else {
				channelCache[channelId] = name
			}
			channelCacheMu.Unlock()
			return name
		}
	}
	return "newapi"
}

// fireUpstreamEvent posts the event to the OTLP bridge asynchronously.
// Errors are silently ignored (fire-and-forget).
func fireUpstreamEvent(event upstreamEvent) {
	payload, err := json.Marshal(event)
	if err != nil {
		return
	}
	req, err := http.NewRequest(http.MethodPost, upstreamBridgeURL, bytes.NewReader(payload))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	go func() {
		client := &http.Client{Timeout: 3 * time.Second}
		client.Do(req)
	}()
}

// UpstreamBridgeMiddleware instruments NewAPI relay responses with OpenClaw upstream metrics.
// It wraps the response writer to capture the response body (for usage extraction),
// measures elapsed time, resolves the channel name via Gin context, and fires a fire-and-forget
// POST to the OpenClaw OTLP bridge /v1/upstream endpoint after each response is written.
//
// Safe to attach to any Gin router group. Does NOT interfere with response delivery.
func UpstreamBridgeMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()

		// Wrap the ResponseWriter to capture written bytes.
		blw := &bufferedWriter{ResponseWriter: c.Writer, buf: &bytes.Buffer{}}
		c.Writer = blw

		c.Next()

		elapsedMs := time.Since(start).Milliseconds()
		statusCode := c.Writer.Status()
		isError := statusCode >= 400

		event := upstreamEvent{
			Channel:      resolveChannelName(c),
			HTTPStatus:   statusCode,
			DurationMs:   elapsedMs,
			StatusFamily: "success",
			IsError:      isError,
		}
		if isError {
			event.StatusFamily = "error"
		}

		// Extract usage from response body (skip chunks and bodies > 1MB).
		if blw.buf.Len() > 0 && blw.buf.Len() < 1<<20 {
			var resp openAIRespPayload
			if err := json.Unmarshal(blw.buf.Bytes(), &resp); err == nil && resp.Usage != nil {
				event.TokensInput = resp.Usage.PromptTokens
				event.TokensOutput = resp.Usage.CompletionTokens
				event.TokensTotal = resp.Usage.TotalTokens
			}
		}

		fireUpstreamEvent(event)
	}
}

// bufferedWriter wraps gin.ResponseWriter to capture response bytes.
type bufferedWriter struct {
	gin.ResponseWriter
	buf *bytes.Buffer
}

func (w *bufferedWriter) Write(b []byte) (int, error) {
	w.buf.Write(b)
	return w.ResponseWriter.Write(b)
}

func (w *bufferedWriter) WriteString(s string) (int, error) {
	w.buf.WriteString(s)
	if sw, ok := w.ResponseWriter.(interface{ WriteString(string) (int, error) }); ok {
		return sw.WriteString(s)
	}
	return w.ResponseWriter.Write([]byte(s))
}

func (w *bufferedWriter) CloseNotify() <-chan bool {
	if cn, ok := w.ResponseWriter.(interface{ CloseNotify() <-chan bool }); ok {
		return cn.CloseNotify()
	}
	ch := make(chan bool, 1)
	return ch
}

func (w *bufferedWriter) Flush() {
	if f, ok := w.ResponseWriter.(interface{ Flush() }); ok {
		f.Flush()
	}
}

func (w *bufferedWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	if h, ok := w.ResponseWriter.(http.Hijacker); ok {
		return h.Hijack()
	}
	return nil, nil, nil
}