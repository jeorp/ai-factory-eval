package fixture
import "testing"
func TestRegression(t *testing.T) { b:=map[string]string{"a":"old"}; got,err:=Merge(b,b,b); if err!=nil || got["a"]!="old" { t.Fatal(got,err) } }
