package fixture

import "testing"

func TestAtomicRollback(t *testing.T) {
    l := &Ledger{Balances: map[string]int{"a":10,"b":0}, Seen: map[string]bool{"old":true}}
    err := l.ApplyBatch([]Transfer{{"one","a","b",4},{"two","a","b",9}})
    if err == nil || l.Balances["a"] != 10 || l.Balances["b"] != 0 || !l.Seen["old"] || l.Seen["one"] { t.Fatal("failed batch changed ledger") }
}
func TestDuplicateReplay(t *testing.T) {
    l := &Ledger{Balances: map[string]int{"a":10,"b":0}, Seen: map[string]bool{"done":true}}
    if l.ApplyBatch([]Transfer{{"done","a","b",3}}) == nil || l.Balances["a"] != 10 { t.Fatal("replayed ID accepted") }
    if l.ApplyBatch([]Transfer{{"new","a","b",2},{"new","b","a",1}}) == nil || l.Seen["new"] { t.Fatal("duplicate ID within batch accepted") }
}
func TestOrderedTransfer(t *testing.T) {
    l := &Ledger{Balances: map[string]int{"a":7}}
    if err := l.ApplyBatch([]Transfer{{"one","a","b",5},{"two","b","c",4}}); err != nil { t.Fatal(err) }
    if l.Balances["a"] != 2 || l.Balances["b"] != 1 || l.Balances["c"] != 4 || !l.Seen["one"] || !l.Seen["two"] { t.Fatal("ordered effects missing") }
}
func TestInvalidTransfer(t *testing.T) {
    for _, x := range []Transfer{{"","a","b",1},{"x","a","",1},{"x","a","a",1},{"x","a","b",0}} {
        l := &Ledger{Balances: map[string]int{"a":5}}
        if l.ApplyBatch([]Transfer{x}) == nil || l.Balances["a"] != 5 || len(l.Seen) != 0 { t.Fatal("invalid transfer accepted", x) }
    }
}
