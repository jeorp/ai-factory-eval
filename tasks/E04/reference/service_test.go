package fixture
import "testing"
func TestAdd(t *testing.T) {if Add(2,3)!=5 {t.Fatal("addition broken")}}
func TestValid(t *testing.T) {if Valid(" ") {t.Fatal("whitespace accepted")}}
func TestNormalize(t *testing.T) {if Normalize(" HI ")!="hi" {t.Fatal("normalization broken")}}
func TestLookup(t *testing.T) {if Lookup(nil,"k")!="" {t.Fatal("nil lookup broken")}}
