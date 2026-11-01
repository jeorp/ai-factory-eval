package fixture

import "errors"

type Transfer struct { ID, From, To string; Amount int }
type Ledger struct { Balances map[string]int; Seen map[string]bool }

func (l *Ledger) ApplyBatch(batch []Transfer) error {
    balances := make(map[string]int, len(l.Balances))
    for k, v := range l.Balances { balances[k] = v }
    seen := make(map[string]bool, len(l.Seen))
    for k, v := range l.Seen { seen[k] = v }
    for _, t := range batch {
        if t.ID == "" || t.From == "" || t.To == "" || t.From == t.To || t.Amount <= 0 { return errors.New("invalid transfer") }
        if seen[t.ID] { return errors.New("duplicate transfer") }
        if balances[t.From] < t.Amount { return errors.New("insufficient funds") }
        balances[t.From] -= t.Amount
        balances[t.To] += t.Amount
        seen[t.ID] = true
    }
    l.Balances, l.Seen = balances, seen
    return nil
}
