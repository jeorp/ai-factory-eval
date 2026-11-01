package fixture
import "testing"
func TestRegression(t *testing.T) {if Echo("hello")!="hello" {t.Fatal("echo changed")};if Receive([]byte(`{"text":"legacy"}`)).Text!="legacy" {t.Fatal("legacy request")}}
