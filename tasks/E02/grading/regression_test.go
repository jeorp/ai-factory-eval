package fixture
import "testing"
func TestRegression(t *testing.T) {i:=Load(Save(Legacy()));if i.ID!="1"||Convert(i).Name!="legacy" {t.Fatal("legacy roundtrip")}}
