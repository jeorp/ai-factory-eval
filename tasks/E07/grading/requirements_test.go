package fixture

import (
	"fmt"
	"reflect"
	"sync"
	"testing"
)

type evalTransfer struct {
	id, from, to string
	amount       int
}

func evalCopyMap[K comparable, V any](m map[K]V) map[K]V {
	if m == nil {
		return nil
	}
	out := make(map[K]V, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}
func evalParallel(l *Ledger, transfers []evalTransfer) []error {
	var wg sync.WaitGroup
	start := make(chan struct{})
	errs := make([]error, len(transfers))
	for i, x := range transfers {
		wg.Add(1)
		go func(i int, x evalTransfer) {
			defer wg.Done()
			<-start
			errs[i] = l.Transfer(x.id, x.from, x.to, x.amount)
		}(i, x)
	}
	close(start)
	wg.Wait()
	return errs
}
func evalUnchanged(t *testing.T, l *Ledger, balances map[string]int, seen map[string]bool) {
	t.Helper()
	if !reflect.DeepEqual(l.Balances, balances) || !reflect.DeepEqual(l.Seen, seen) {
		t.Fatalf("failed transfer changed state: balances=%v seen=%v", l.Balances, l.Seen)
	}
}

func TestConcurrentReplay(t *testing.T) {
	for round := 0; round < 12; round++ {
		l := &Ledger{Balances: map[string]int{"a": 100, "b": 0}, Seen: map[string]bool{"old": true}}
		xs := make([]evalTransfer, 48)
		for i := range xs {
			xs[i] = evalTransfer{"same", "a", "b", 1}
		}
		successes := 0
		for _, err := range evalParallel(l, xs) {
			if err == nil {
				successes++
			}
		}
		if successes != 1 || !reflect.DeepEqual(l.Balances, map[string]int{"a": 99, "b": 1}) || !reflect.DeepEqual(l.Seen, map[string]bool{"old": true, "same": true}) {
			t.Fatalf("round %d: successes=%d balances=%v seen=%v", round, successes, l.Balances, l.Seen)
		}
	}
}

func TestConcurrentBalance(t *testing.T) {
	for round := 0; round < 12; round++ {
		l := &Ledger{Balances: map[string]int{"a": 10, "b": 0, "untouched": 7}}
		xs := make([]evalTransfer, 48)
		for i := range xs {
			xs[i] = evalTransfer{fmt.Sprint(i), "a", "b", 1}
		}
		successes := 0
		for i, err := range evalParallel(l, xs) {
			if err == nil {
				successes++
				if !l.Seen[fmt.Sprint(i)] {
					t.Fatal("successful ID missing")
				}
			} else if l.Seen[fmt.Sprint(i)] {
				t.Fatal("failed ID consumed")
			}
		}
		if successes != 10 || !reflect.DeepEqual(l.Balances, map[string]int{"a": 0, "b": 10, "untouched": 7}) || len(l.Seen) != 10 {
			t.Fatalf("round %d: successes=%d balances=%v seen=%v", round, successes, l.Balances, l.Seen)
		}
	}
}

func TestInvalidPreservesState(t *testing.T) {
	invalid := []evalTransfer{{"", "a", "b", 1}, {"x", "", "b", 1}, {"x", "a", "", 1}, {"x", "a", "a", 1}, {"x", "a", "b", 0}, {"x", "a", "b", -1}, {"x", "a", "b", 11}, {"x", "missing", "b", 1}}
	for _, nilBalances := range []bool{false, true} {
		for _, nilSeen := range []bool{false, true} {
			for _, x := range invalid {
				l := &Ledger{}
				if !nilBalances {
					l.Balances = map[string]int{"a": 10, "b": 2, "other": 9}
				}
				if !nilSeen {
					l.Seen = map[string]bool{"old": true, "unused": false}
				}
				balances, seen := evalCopyMap(l.Balances), evalCopyMap(l.Seen)
				if err := l.Transfer(x.id, x.from, x.to, x.amount); err == nil {
					t.Fatalf("invalid transfer accepted: %+v", x)
				}
				evalUnchanged(t, l, balances, seen)
			}
		}
	}
}

func TestFailedIDCanRetry(t *testing.T) {
	l := &Ledger{Balances: map[string]int{"a": 1, "b": 4}}
	if l.Transfer("retry", "a", "b", 2) == nil {
		t.Fatal("insufficient funds accepted")
	}
	evalUnchanged(t, l, map[string]int{"a": 1, "b": 4}, nil)
	if l.Transfer("fund", "b", "a", 2) != nil || l.Transfer("retry", "a", "b", 2) != nil {
		t.Fatal("failed ID could not be retried after funding")
	}
	if !reflect.DeepEqual(l.Balances, map[string]int{"a": 1, "b": 4}) || !reflect.DeepEqual(l.Seen, map[string]bool{"retry": true, "fund": true}) {
		t.Fatal("retry effects incorrect")
	}
}

func TestDuplicatePreservesState(t *testing.T) {
	l := &Ledger{Balances: map[string]int{"a": 20, "b": 10, "c": 10}, Seen: map[string]bool{"done": true}}
	for _, x := range []evalTransfer{{"done", "a", "b", 1}, {"done", "b", "c", 2}, {"done", "a", "b", 100}} {
		balances, seen := evalCopyMap(l.Balances), evalCopyMap(l.Seen)
		if l.Transfer(x.id, x.from, x.to, x.amount) == nil {
			t.Fatal("processed ID reused")
		}
		evalUnchanged(t, l, balances, seen)
	}
}

func TestIndependentLedgersAndIDs(t *testing.T) {
	ledgers := []*Ledger{{Balances: map[string]int{"a": 10, "b": 0}}, {Balances: map[string]int{"a": 10, "b": 0}}}
	for _, l := range ledgers {
		for _, id := range []string{"first", "second", "third"} {
			if l.Transfer(id, "a", "b", 1) != nil {
				t.Fatal("independent ID rejected")
			}
		}
		for _, id := range []string{"first", "second", "third"} {
			if l.Transfer(id, "a", "b", 1) == nil {
				t.Fatal("earlier ID forgotten")
			}
		}
		if !reflect.DeepEqual(l.Balances, map[string]int{"a": 7, "b": 3}) || len(l.Seen) != 3 {
			t.Fatal("ledger state incorrect")
		}
	}
}

func TestTransferSequence(t *testing.T) {
	l := &Ledger{Balances: map[string]int{"a": 100, "b": 30, "c": 10}}
	balances, seen := evalCopyMap(l.Balances), map[string]bool(nil)
	for i := 0; i < 160; i++ {
		accounts := []string{"a", "b", "c"}
		x := evalTransfer{fmt.Sprint(i % 23), accounts[i%3], accounts[(i+1)%3], 1 + i%17}
		if i%7 == 0 {
			x.amount = 0
		}
		if i%11 == 0 {
			x.to = x.from
		}
		valid := x.from != x.to && x.amount > 0 && balances[x.from] >= x.amount && !seen[x.id]
		err := l.Transfer(x.id, x.from, x.to, x.amount)
		if (err == nil) != valid {
			t.Fatalf("step %d: success=%v want=%v", i, err == nil, valid)
		}
		if valid {
			if seen == nil {
				seen = map[string]bool{}
			}
			balances[x.from] -= x.amount
			balances[x.to] += x.amount
			seen[x.id] = true
		}
		if !reflect.DeepEqual(l.Balances, balances) || !reflect.DeepEqual(l.Seen, seen) {
			t.Fatalf("step %d: state differs from independent model", i)
		}
	}
}

// The trusted grader runs this entry point with go test -race, separately from functional tests.
func TestRace(t *testing.T) {
	for round := 0; round < 4; round++ {
		l := &Ledger{Balances: map[string]int{"a": 256, "b": 0}}
		xs := make([]evalTransfer, 128)
		for i := range xs {
			xs[i] = evalTransfer{fmt.Sprint(i), "a", "b", 1}
		}
		for _, err := range evalParallel(l, xs) {
			if err != nil {
				t.Fatal("unique transfer failed", err)
			}
		}
		if l.Balances["a"] != 128 || l.Balances["b"] != 128 || len(l.Seen) != 128 {
			t.Fatal("lost concurrent update")
		}
	}
}
