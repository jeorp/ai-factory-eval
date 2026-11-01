package fixture
import "testing"
func TestRegression(t *testing.T) { l:=&Ledger{Balances:map[string]int{"a":5}}; if l.Transfer("x","a","b",3)!=nil || l.Balances["a"]!=2 || l.Balances["b"]!=3 { t.Fatal("valid transfer") }; if l.Transfer("y","a","b",3)==nil || l.Seen["y"] || l.Balances["a"]!=2 { t.Fatal("invalid transfer changed state") } }
