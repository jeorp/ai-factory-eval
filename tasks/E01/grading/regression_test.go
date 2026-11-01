package fixture

import "testing"

func TestRegression(t *testing.T) {
	TestDefaultLanguage(t)
	if Echo("hello") != "hello" {
		t.Fatal("echo changed")
	}
	if Receive([]byte(`{"text":"legacy"}`)).Text != "legacy" {
		t.Fatal("legacy request")
	}
}
