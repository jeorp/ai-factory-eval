package fixture
import ("testing";"encoding/json";"strings";"reflect")
var _ = json.Marshal
var _ = strings.Contains
var _ = reflect.TypeOf
func TestArithmetic(t *testing.T) {for a:=-3;a<=3;a++ {for b:=-3;b<=3;b++ {if Add(a,b)!=a+b {t.Fatal("addition")}}}}
func TestValidation(t *testing.T) {if Valid(" \t\n")||!Valid("x") {t.Fatal("validation")}}
func TestNormalization(t *testing.T) {if Normalize(" HeLLo ")!="hello" {t.Fatal("normalization")}}
func TestNilLookup(t *testing.T) {if Lookup(nil,"unknown")!=""||Lookup(map[string]string{"x":"y"},"x")!="y" {t.Fatal("lookup")}}
