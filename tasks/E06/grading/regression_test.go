package fixture
import "testing"
func TestRegression(t *testing.T) { if Total(Order{nil,0})!=0 || Total(Order{[]int{12},0})!=12 { t.Fatal("existing totals changed") } }
