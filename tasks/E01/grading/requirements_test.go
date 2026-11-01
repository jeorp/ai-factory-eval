package fixture

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

var _ = json.Marshal
var _ = strings.Contains
var _ = reflect.TypeOf

func TestPrompts(t *testing.T) {
	for _, k := range []string{"chat", "plan", "review", "implement", "verify"} {
		p := Prompts[k]
		if len(p) < 10 {
			t.Errorf("missing prompt: %s", k)
		}
		for _, r := range p {
			if r > 127 {
				t.Errorf("non-English prompt: %s", k)
				break
			}
		}
	}
}
func TestReceive(t *testing.T) {
	for _, lang := range []string{"en", "ja"} {
		s := Receive([]byte(`{"text":"hello","language":"` + lang + `"}`))
		if s.Language != lang {
			t.Errorf("API lost language: %s", lang)
		}
	}
}
func TestPersistence(t *testing.T) {
	for _, lang := range []string{"en", "ja"} {
		s := Load(Save(Session{Text: "hello", Language: lang}))
		if s.Language != lang || s.Text != "hello" {
			t.Errorf("roundtrip: %s", lang)
		}
	}
}
func TestCalls(t *testing.T) {
	for _, lang := range []string{"en", "ja"} {
		calls := Calls(Session{Language: lang})
		for _, k := range []string{"chat", "plan", "review", "implement", "verify"} {
			if calls[k].Language != lang || calls[k].Prompt != Prompts[k] {
				t.Errorf("call %s lost %s", k, lang)
			}
		}
	}
}

// README documents ja when language is omitted, including pre-language sessions.
func TestDefaultLanguage(t *testing.T) {
	cases := map[string]Session{
		"omitted-request":       Receive([]byte(`{"text":"legacy"}`)),
		"saved-omitted-request": Load(Save(Receive([]byte(`{"text":"legacy"}`)))),
		"legacy-saved-session":  Load([]byte(`{"text":"legacy"}`)),
	}
	for name, s := range cases {
		t.Run(name, func(t *testing.T) {
			if s.Text != "legacy" {
				t.Fatal("legacy text lost")
			}
			calls := Calls(s)
			for _, kind := range []string{"chat", "plan", "review", "implement", "verify"} {
				if calls[kind].Language != "ja" {
					t.Errorf("%s: got %q, want ja", kind, calls[kind].Language)
				}
			}
		})
	}
}
