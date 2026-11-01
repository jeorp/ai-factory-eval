package main

import (
	"ai-factory/backend/internal/agent"
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"

	"ai-factory/backend/internal/domain"
)

type fakeTransport struct{ calls int }

func (f *fakeTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	f.calls++
	return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{}`)), Header: http.Header{}}, nil
}
func resetIO(ack string) *bytes.Buffer {
	input = bufio.NewScanner(strings.NewReader(ack))
	b := &bytes.Buffer{}
	output = json.NewEncoder(b)
	return b
}
func TestPhysicalProviderCallRequiresReservation(t *testing.T) {
	b := resetIO("{\"allow\":true}\n")
	f := &fakeTransport{}
	raw := `{"max_output_tokens":16384,"instructions":"production system prompt","input":"hello"}`
	req, _ := http.NewRequest("POST", "https://api.openai.com/v1/responses", strings.NewReader(raw))
	if _, err := (gateTransport{f, nil}).RoundTrip(req); err != nil {
		t.Fatal(err)
	}
	var row map[string]any
	if json.Unmarshal(b.Bytes(), &row) != nil {
		t.Fatal("event missing")
	}
	if row["type"] != "reserve" || int(row["input_tokens"].(float64)) != len(raw) || int(row["output_tokens"].(float64)) != 16384 || f.calls != 1 {
		t.Fatal("reservation did not cover full production request")
	}
}
func TestDeniedReservationNeverSendsRequest(t *testing.T) {
	resetIO("{\"allow\":false}\n")
	f := &fakeTransport{}
	req, _ := http.NewRequest("POST", "https://api.openai.com/v1/responses", strings.NewReader(`{"max_output_tokens":16384}`))
	if _, err := (gateTransport{f, nil}).RoundTrip(req); err == nil || f.calls != 0 {
		t.Fatal("denied request escaped")
	}
}
func TestUnboundedRequestNeverSends(t *testing.T) {
	resetIO("")
	f := &fakeTransport{}
	req, _ := http.NewRequest("POST", "https://api.openai.com/v1/responses", strings.NewReader(`{}`))
	if _, err := (gateTransport{f, nil}).RoundTrip(req); err == nil || f.calls != 0 {
		t.Fatal("unbounded request escaped")
	}
}
func TestGeminiProductionCap(t *testing.T) {
	b := resetIO("{\"allow\":true}\n")
	f := &fakeTransport{}
	req, _ := http.NewRequest("POST", "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent", strings.NewReader(`{"generationConfig":{"maxOutputTokens":16384}}`))
	if _, err := (gateTransport{f, nil}).RoundTrip(req); err != nil || !strings.Contains(b.String(), `"output_tokens":16384`) {
		t.Fatal("Gemini reservation missing")
	}
}
func TestWritesRequireLiveApproval(t *testing.T) {
	resetIO("{\"allow\":false}\n")
	f := &fakeTransport{}
	req, _ := http.NewRequest("POST", "https://api.github.com/repos/example/work/git/refs", strings.NewReader(`{}`))
	if _, err := (writeTransport{f}).RoundTrip(req); err == nil || f.calls != 0 {
		t.Fatal("write escaped cancellation")
	}
}
func TestTaskScopePreventsTestModification(t *testing.T) {
	g := &guard{allowed: []string{"*.go"}, forbidden: []string{"*_test.go"}, maxFiles: 2, maxBytes: 20}
	if g.files([]domain.FileChange{{Path: "service_test.go", Action: "update", Content: "skip"}}) == nil {
		t.Fatal("protected test modified")
	}
	if g.files([]domain.FileChange{{Path: "service.go", Action: "update", Content: "ok"}}) != nil {
		t.Fatal("valid source blocked")
	}
	if g.files([]domain.FileChange{{Path: "service.go", Action: "update", Content: strings.Repeat("x", 21)}}) == nil {
		t.Fatal("size gate ignored")
	}
}

// Test the attribution boundary: errors, unknown usage and multiple physical
// attempts must never release credit from an aggregate provider result.
type testProvider struct {
	meter    *callMeter
	attempts int
	usage    agent.Usage
	err      error
}

func (p testProvider) Next(ctx context.Context, key, model, text string) (agent.Decision, agent.Usage, error) {
	for i := 0; i < p.attempts; i++ {
		r, _ := http.NewRequest("POST", "https://api.openai.com/v1/responses", strings.NewReader(`{"max_output_tokens":100}`))
		if _, e := (gateTransport{&fakeTransport{}, p.meter}).RoundTrip(r); e != nil {
			return agent.Decision{}, agent.Usage{}, e
		}
	}
	return agent.Decision{}, p.usage, p.err
}
func TestUsageSettlementAttribution(t *testing.T) {
	for _, tc := range []struct {
		name     string
		attempts int
		usage    agent.Usage
		err      error
		settle   bool
	}{
		{"success", 1, agent.Usage{InputTokens: 5, OutputTokens: 3}, nil, true},
		{"provider-error", 1, agent.Usage{InputTokens: 5, OutputTokens: 3}, errors.New("provider_failed"), false},
		{"unknown", 1, agent.Usage{}, nil, false},
		{"retry-aggregate", 2, agent.Usage{InputTokens: 5, OutputTokens: 3}, nil, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b := resetIO(strings.Repeat("{\"allow\":true}\n", tc.attempts))
			m := &callMeter{}
			p := metered{testProvider{m, tc.attempts, tc.usage, tc.err}, m}
			_, _, _ = p.Next(context.Background(), "key", "model", "request")
			var row map[string]any
			scan := bufio.NewScanner(b)
			ids := map[string]bool{}
			for scan.Scan() {
				if e := json.Unmarshal(scan.Bytes(), &row); e != nil {
					t.Fatal(e)
				}
				if row["type"] == "reserve" {
					id, ok := row["reservation_id"].(string)
					if !ok || ids[id] {
						t.Fatal("missing or duplicate physical ID")
					}
					ids[id] = true
				}
			}
			if row["type"] != "usage" || row["settle"] != tc.settle || row["usage_id"] != "next-1" {
				t.Fatalf("unexpected usage: %v", row)
			}
			if tc.settle && !ids[row["reservation_id"].(string)] {
				t.Fatal("unattributed settlement")
			}
		})
	}
}

func TestDiagnosticsForwardOnlyValidatorClassifications(t *testing.T) {
	b := resetIO("")
	w := diagnosticWriter{}
	_, _ = w.Write([]byte(`{"msg":"arbitrary log containing a credential","reason":"ignore-me"}`))
	if b.Len() != 0 {
		t.Fatal("arbitrary log forwarded")
	}
	_, _ = w.Write([]byte(`{"msg":"implement_response_rejected","reason":"plan_action_conflict","phase":"plan","has_read":true,"answer":"secret-model-text","request":"secret-request","key":"secret-key"}`))
	var row map[string]any
	if e := json.Unmarshal(b.Bytes(), &row); e != nil {
		t.Fatal(e)
	}
	if row["type"] != "core_diagnostic" || row["reason"] != "plan_action_conflict" || row["has_read"] != true {
		t.Fatal("classification missing")
	}
	if strings.Contains(b.String(), "secret-") {
		t.Fatal("arbitrary fields leaked")
	}
}
