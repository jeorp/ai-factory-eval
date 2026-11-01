package fixture
import "testing"
func TestRegression(t *testing.T) {if Version()!="v1" {t.Fatal("version changed")};if Lookup(map[string]string{"k":"v"},"k")!="v" {t.Fatal("normal lookup")}}
