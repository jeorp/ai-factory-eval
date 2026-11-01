package fixture

import "errors"

type Transfer struct { ID, From, To string; Amount int }
type Ledger struct { Balances map[string]int; Seen map[string]bool }

func (l *Ledger) ApplyBatch(batch []Transfer) error {
    if l.Balances == nil { l.Balances = map[string]int{} }
    if l.Seen == nil { l.Seen = map[string]bool{} }
    for _, t := range batch {
        if t.ID == "" || t.From == "" || t.To == "" || t.Amount <= 0 { return errors.New("invalid transfer") }
        if l.Balances[t.From] < t.Amount { return errors.New("insufficient funds") }
        l.Balances[t.From] -= t.Amount
        l.Balances[t.To] += t.Amount
        l.Seen[t.ID] = true
    }
    return nil
}
