package fixture

import "testing"

func TestRegression(t *testing.T) {
    l := &Ledger{Balances: map[string]int{"a":8}}
    if err := l.ApplyBatch([]Transfer{{"x","a","b",3}}); err != nil { t.Fatal(err) }
    if l.Balances["a"] != 5 || l.Balances["b"] != 3 || !l.Seen["x"] { t.Fatal("existing successful transfer broken") }
    if err := l.ApplyBatch(nil); err != nil { t.Fatal(err) }
}
