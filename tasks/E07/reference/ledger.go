package fixture
import ("errors"; "sync")
type Ledger struct { Balances map[string]int; Seen map[string]bool; mu sync.Mutex }
func (l *Ledger) Transfer(id, from, to string, amount int) error {
 l.mu.Lock(); defer l.mu.Unlock()
 if id=="" || from=="" || to=="" || from==to || amount<=0 || l.Balances[from]<amount { return errors.New("invalid transfer") }
 if l.Seen[id] { return errors.New("duplicate") }
 if l.Seen==nil { l.Seen=map[string]bool{} }
 l.Balances[from]-=amount; l.Balances[to]+=amount; l.Seen[id]=true
 return nil
}
