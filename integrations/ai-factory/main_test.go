package main

import (
	"bufio"
	"bytes"
	"encoding/json"
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
	if _, err := (gateTransport{f}).RoundTrip(req); err != nil {
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
	if _, err := (gateTransport{f}).RoundTrip(req); err == nil || f.calls != 0 {
		t.Fatal("denied request escaped")
	}
}
func TestUnboundedRequestNeverSends(t *testing.T) {
	resetIO("")
	f := &fakeTransport{}
	req, _ := http.NewRequest("POST", "https://api.openai.com/v1/responses", strings.NewReader(`{}`))
	if _, err := (gateTransport{f}).RoundTrip(req); err == nil || f.calls != 0 {
		t.Fatal("unbounded request escaped")
	}
}
func TestGeminiProductionCap(t *testing.T) {
	b := resetIO("{\"allow\":true}\n")
	f := &fakeTransport{}
	req, _ := http.NewRequest("POST", "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent", strings.NewReader(`{"generationConfig":{"maxOutputTokens":16384}}`))
	if _, err := (gateTransport{f}).RoundTrip(req); err != nil || !strings.Contains(b.String(), `"output_tokens":16384`) {
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
