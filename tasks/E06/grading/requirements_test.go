package fixture
import "testing"
func TestDiscountedTotal(t *testing.T) { if got:=Total(Order{[]int{120,80},30}); got!=170 { t.Fatalf("got %d",got) }; if got:=Total(Order{[]int{7,3},2}); got!=8 { t.Fatalf("got %d",got) } }
func TestNoDiscount(t *testing.T) { if Total(Order{[]int{10,5},0})!=15 { t.Fatal("normal total") } }
