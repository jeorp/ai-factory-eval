// Command factory-eval is copied into a pinned ai-factory/backend checkout.
// It invokes the production Runner and providers without changing their prompts.
package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path"
	"regexp"
	"strings"
	"time"

	"ai-factory/backend/internal/agent"
	"ai-factory/backend/internal/domain"
	gh "ai-factory/backend/internal/github"
)

const version = "factory-eval/v1"

var input = bufio.NewScanner(os.Stdin)
var output = json.NewEncoder(os.Stdout)

func emit(kind string, fields map[string]any) {
	fields["version"] = version
	fields["type"] = kind
	_ = output.Encode(fields)
}

type memory map[string]string

func (m memory) Get(_ context.Context, k string) (string, bool) { v, ok := m[k]; return v, ok }
func (m memory) Put(_ context.Context, k, v string)             { m[k] = v }

type command struct {
	Version    string   `json:"version"`
	Action     string   `json:"action"`
	RunID      string   `json:"run_id"`
	TaskID     string   `json:"task_id"`
	Repository string   `json:"repository"`
	BaseCommit string   `json:"base_commit"`
	BaseBranch string   `json:"base_branch"`
	Request    string   `json:"request"`
	Provider   string   `json:"provider"`
	Model      string   `json:"model"`
	Allowed    []string `json:"allowed_paths"`
	Forbidden  []string `json:"forbidden_paths"`
	MaxFiles   int      `json:"max_files"`
	MaxBytes   int      `json:"max_bytes"`
	Seconds    int      `json:"seconds"`
	PullNumber int      `json:"pull_number"`
}
type guard struct {
	*gh.Client
	repository, initial, branch string
	allowed, forbidden          []string
	maxFiles, maxBytes          int
	changes                     map[string]domain.FileChange
	published                   string
}

func (g *guard) permits(p string) bool {
	if domain.ValidateChangePath(p) != nil {
		return false
	}
	for _, pattern := range g.forbidden {
		if match, _ := path.Match(pattern, p); match {
			return false
		}
	}
	for _, pattern := range g.allowed {
		if match, _ := path.Match(pattern, p); match {
			return true
		}
	}
	return false
}
func (g *guard) files(files []domain.FileChange) error {
	if len(files) == 0 || len(files) > g.maxFiles {
		return errors.New("scope_rejected")
	}
	total := 0
	seen := map[string]bool{}
	for _, f := range files {
		if seen[f.Path] || !g.permits(f.Path) {
			return errors.New("scope_rejected")
		}
		seen[f.Path] = true
		total += len(f.Content)
		if f.Action != "create" && f.Action != "update" && f.Action != "delete" {
			return errors.New("scope_rejected")
		}
		for _, s := range []string{os.Getenv("EVAL_MODEL_KEY"), os.Getenv("EVAL_WORK_TOKEN")} {
			if s != "" && strings.Contains(f.Content, s) {
				return errors.New("credential_echo_blocked")
			}
		}
	}
	if total > g.maxBytes {
		return errors.New("scope_rejected")
	}
	return nil
}
func (g *guard) Read(ctx context.Context, tok string, t domain.Target, r gh.Read) (string, domain.Source, int, error) {
	if t.Repository != g.repository {
		return "", domain.Source{}, 0, errors.New("scope_rejected")
	}
	return g.Client.Read(ctx, tok, t, r)
}
func (g *guard) Authorize(ctx context.Context, tok, repo string) (gh.Repository, error) {
	if repo != g.repository {
		return gh.Repository{}, errors.New("scope_rejected")
	}
	return g.Client.Authorize(ctx, tok, repo)
}
func (g *guard) Commit(ctx context.Context, tok string, r gh.Repository) (string, error) {
	if r.FullName != g.repository {
		return "", errors.New("scope_rejected")
	}
	return g.initial, nil
}
func (g *guard) RawFile(ctx context.Context, tok, repo, sha, p string) (string, bool, error) {
	if repo != g.repository {
		return "", false, errors.New("scope_rejected")
	}
	return g.Client.RawFile(ctx, tok, repo, sha, p)
}
func (g *guard) FileList(ctx context.Context, tok, repo, sha string) ([]string, bool, error) {
	if repo != g.repository {
		return nil, false, errors.New("scope_rejected")
	}
	return g.Client.FileList(ctx, tok, repo, sha)
}
func (g *guard) PutChange(ctx context.Context, id string, v int, f domain.FileChange) error {
	if !g.permits(f.Path) {
		return errors.New("scope_rejected")
	}
	if err := g.Client.PutChange(ctx, id, v, f); err != nil {
		return err
	}
	g.changes[f.Path] = f
	return nil
}
func (g *guard) Publish(ctx context.Context, tok string, r gh.PublishRequest, s gh.PublishState, save func(gh.PublishState) error) (gh.PublishState, error) {
	if r.Repository != g.repository || r.Branch != g.branch || r.Branch == r.BaseBranch || r.BaseCommit != g.initial && r.BaseCommit != g.published {
		return s, errors.New("scope_rejected")
	}
	if err := g.files(r.Files); err != nil {
		return s, err
	}
	out, err := g.Client.Publish(ctx, tok, r, s, func(cp gh.PublishState) error {
		if cp.Commit != "" {
			g.published = cp.Commit
		}
		return save(cp)
	})
	if out.Commit != "" {
		g.published = out.Commit
	}
	return out, err
}

// Every physical provider request is reserved before any bytes leave this process.
// Serialized request bytes conservatively upper-bound input tokens including the
// production system prompt. Output reservation uses the production API cap.
type gateTransport struct{ base http.RoundTripper }

func (t gateTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	raw, err := io.ReadAll(io.LimitReader(r.Body, 2*1024*1024))
	if err != nil {
		return nil, err
	}
	_ = r.Body.Close()
	r.Body = io.NopCloser(bytes.NewReader(raw))
	var body map[string]any
	if json.Unmarshal(raw, &body) != nil {
		return nil, errors.New("invalid_provider_request")
	}
	maximum := 0
	if x, ok := body["max_output_tokens"].(float64); ok {
		maximum = int(x)
	}
	if x, ok := body["generationConfig"].(map[string]any); ok {
		if n, ok := x["maxOutputTokens"].(float64); ok {
			maximum = int(n)
		}
	}
	if maximum <= 0 || len(raw) >= 2*1024*1024 {
		return nil, errors.New("unbounded_provider_request")
	}
	emit("reserve", map[string]any{"input_tokens": len(raw), "output_tokens": maximum})
	if !input.Scan() {
		return nil, errors.New("budget_denied")
	}
	var reply struct {
		Allow bool `json:"allow"`
	}
	if json.Unmarshal(input.Bytes(), &reply) != nil || !reply.Allow {
		return nil, errors.New("budget_denied")
	}
	return t.base.RoundTrip(r)
}

type writeTransport struct{ base http.RoundTripper }

func (t writeTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	if r.URL.Host == "api.github.com" && r.Method != "GET" && r.Method != "HEAD" {
		emit("write_intent", map[string]any{})
		if !input.Scan() {
			return nil, errors.New("cancelled")
		}
		var reply struct {
			Allow bool `json:"allow"`
		}
		if json.Unmarshal(input.Bytes(), &reply) != nil || !reply.Allow {
			return nil, errors.New("cancelled")
		}
	}
	return t.base.RoundTrip(r)
}

type metered struct{ inner agent.Provider }

func (p metered) Next(ctx context.Context, key, model, request string) (agent.Decision, agent.Usage, error) {
	d, u, e := p.inner.Next(ctx, key, model, request)
	emit("usage", map[string]any{"input_tokens": u.InputTokens, "output_tokens": u.OutputTokens, "usage_known": u.InputTokens != 0 || u.OutputTokens != 0})
	return d, u, e
}

var errorCode = regexp.MustCompile(`^[a-z_0-9]+$`)

func safeError(e error) string {
	if e == nil {
		return ""
	}
	if errorCode.MatchString(e.Error()) {
		return e.Error()
	}
	return "core_error"
}
func snapshotChanges(ctx context.Context, writer *guard, i *domain.Implementation) []map[string]any {
	changes := []map[string]any{}
	if writer == nil || i == nil {
		return changes
	}
	for _, s := range i.Changes {
		f, ok := writer.GetChange(ctx, i.CacheID, i.Version, s.Path)
		if ok {
			hash := sha256.Sum256([]byte(f.Content))
			changes = append(changes, map[string]any{"path": f.Path, "action": f.Action, "content": f.Content, "sha256": hex.EncodeToString(hash[:]), "base_commit": i.BaseCommit})
		}
	}
	return changes
}

func main() {
	input.Buffer(make([]byte, 65536), 4*1024*1024)
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	slog.SetDefault(logger)
	var runner *agent.Runner
	var state domain.Session
	var writer *guard
	var latest agent.Checkpoint
	var cancel context.CancelFunc
	ctx := context.Background()
	for input.Scan() {
		var c command
		if json.Unmarshal(input.Bytes(), &c) != nil || c.Version != version {
			emit("result", map[string]any{"error": "protocol_mismatch"})
			continue
		}
		if c.Action == "hello" {
			emit("result", map[string]any{"capabilities": map[string]bool{"real_runner": true, "native_ci": nativeCI, "pre_call_budget": true, "draft_pr": true, "followup": true}})
			continue
		}
		var err error
		switch c.Action {
		case "start":
			if runner != nil || c.Seconds <= 0 || c.MaxFiles <= 0 || c.MaxBytes <= 0 || !domain.ValidCommit(c.BaseCommit) || !regexp.MustCompile(`^[a-f0-9]{24}$`).MatchString(c.RunID) {
				err = errors.New("invalid_input")
				break
			}
			target, e := domain.ParseTarget(c.Repository)
			if e != nil {
				err = e
				break
			}
			target.Commit = c.BaseCommit
			modelKey, token := os.Getenv("EVAL_MODEL_KEY"), os.Getenv("EVAL_WORK_TOKEN")
			if modelKey == "" || token == "" {
				err = errors.New("credentials_missing")
				break
			}
			ctx, cancel = context.WithTimeout(context.Background(), time.Duration(c.Seconds)*time.Second)
			defer cancel()
			httpModel := &http.Client{Transport: gateTransport{http.DefaultTransport}, Timeout: 120 * time.Second}
			var provider agent.Provider
			switch c.Provider {
			case "openai":
				provider = &agent.OpenAI{HTTP: httpModel}
			case "gemini":
				provider = &agent.Gemini{HTTP: httpModel}
			default:
				err = errors.New("provider_unsupported")
			}
			if err != nil {
				break
			}
			client := &gh.Client{HTTP: &http.Client{Transport: writeTransport{http.DefaultTransport}, Timeout: 60 * time.Second}, Cache: memory{}}
			writer = &guard{Client: client, repository: c.Repository, initial: c.BaseCommit, branch: "ai-factory/eval-" + c.RunID + "-work", allowed: c.Allowed, forbidden: c.Forbidden, maxFiles: c.MaxFiles, maxBytes: c.MaxBytes, changes: map[string]domain.FileChange{}}
			runner = &agent.Runner{AI: metered{provider}, GitHub: writer, Writer: writer}
			state = domain.Session{ID: "eval-" + c.RunID, Target: target, RunKind: "implement", Model: c.Model}
			state.Implementation = agent.StartImplementation(nil, agent.ImplementRequest{Request: c.Request, PullNumber: c.PullNumber}, agent.NewCacheID())
			state.Implementation.BaseCommit = c.BaseCommit
			state.Implementation.BaseBranch = c.BaseBranch
		case "approve":
			if runner == nil || state.Implementation == nil || state.Implementation.Status != "plan_ready" || state.Implementation.Plan == nil {
				err = errors.New("plan_missing")
				break
			}
			i := state.Implementation
			if i.Clarification != "" {
				err = errors.New("clarification_required")
				break
			}
			files := []domain.FileChange{}
			for _, f := range i.Plan.Files {
				files = append(files, domain.FileChange{Path: f.Path, Action: f.Action})
			}
			if err = writer.files(files); err != nil {
				break
			}
			i.Status = "editing"
			i.NextStep = 0
			if !i.Revising {
				i.Changes = []domain.ChangeSummary{}
				i.Unverified = []string{}
			}
			i.Verification = nil
		case "amend":
			if runner == nil || state.Implementation == nil || state.Implementation.Status != "amend_ready" {
				err = errors.New("amendment_missing")
				break
			}
			files := []domain.FileChange{}
			for _, f := range state.Implementation.PendingFiles {
				files = append(files, domain.FileChange{Path: f.Path, Action: f.Action})
			}
			if err = writer.files(files); err != nil {
				break
			}
			state.Implementation.ResolveAmendment(true)
		case "followup":
			if runner == nil || state.Implementation.Status != "ready" {
				err = errors.New("draft_missing")
				break
			}
			state.Implementation = agent.StartImplementation(state.Implementation, agent.ImplementRequest{Request: c.Request}, agent.NewCacheID())
		case "publish":
			if runner == nil || state.Implementation.Status != "ready" {
				err = errors.New("draft_missing")
				break
			}
			state.Implementation.Status = "publishing"
			state.Implementation.Branch = writer.branch
		case "check_ci":
			if runner == nil || !nativeCI {
				err = errors.New("ci_capability_unavailable")
				break
			}
			state.Implementation.Status = "checking_ci"
		case "fix_ci":
			if runner == nil {
				err = errors.New("session_missing")
				break
			}
			state.Implementation, err = startCIFix(state.Implementation)
		default:
			err = errors.New("unknown_action")
		}
		if err == nil {
			err = runner.Run(ctx, agent.Input{Session: state, Key: os.Getenv("EVAL_MODEL_KEY"), GitHubToken: os.Getenv("EVAL_WORK_TOKEN"), Logger: logger, ContinueMetrics: true}, func(cp agent.Checkpoint) error {
				latest = cp
				// Keep server-only cache references in process, matching the production handler.
				state.Implementation = cp.Implementation
				state.Metrics = cp.Metrics
				state.Summary = cp.Summary
				state.Sources = cp.Sources
				state.Limitations = cp.Limitations
				emit("checkpoint", map[string]any{"checkpoint": cp, "changes": snapshotChanges(ctx, writer, cp.Implementation)})
				return nil
			})
		}
		changes := snapshotChanges(ctx, writer, state.Implementation)
		emit("result", map[string]any{"error": safeError(err), "implementation": state.Implementation, "changes": changes, "metrics": latest.Metrics})
	}
	if err := input.Err(); err != nil {
		fmt.Fprintln(os.Stderr, "input_error")
	}
}
