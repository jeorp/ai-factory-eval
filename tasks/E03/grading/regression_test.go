package fixture
import "testing"
func TestRegression(t *testing.T) {if Identity("same")!="same" {t.Fatal("identity")};c,e:=Apply("old",true,nil,"new");if e!=nil||c.Action!="update" {t.Fatal("existing update")}}
