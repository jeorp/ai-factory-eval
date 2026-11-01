package fixture
import ("testing";"encoding/json";"strings";"reflect")
var _ = json.Marshal
var _ = strings.Contains
var _ = reflect.TypeOf
func TestField(t *testing.T) {typ:=reflect.TypeOf(Item{});f,ok:=typ.FieldByName("Label");if !ok||f.Type.Kind()!=reflect.String {t.Fatal("Label string missing")}}
func TestConversion(t *testing.T) {i:=Load([]byte(`{"id":"1","name":"n","label":"urgent"}`));b,_:=json.Marshal(Convert(i));if !strings.Contains(string(b),`"label":"urgent"`) {t.Fatal("conversion lost label")}}
func TestStorage(t *testing.T) {i:=Load([]byte(`{"id":"1","label":"urgent"}`));if !strings.Contains(string(Save(i)),`"label":"urgent"`) {t.Fatal("storage lost label")}}
func TestCompatibility(t *testing.T) {i:=Legacy();if i.ID!="1"||i.Name!="legacy" {t.Fatal("legacy broken")};if !strings.Contains(string(Save(i)),`"label"`) {return};t.Fatal("empty label must be optional")}
