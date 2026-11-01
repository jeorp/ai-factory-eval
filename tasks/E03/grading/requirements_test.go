package fixture
import ("testing";"encoding/json";"strings";"reflect")
var _ = json.Marshal
var _ = strings.Contains
var _ = reflect.TypeOf
func TestCreate(t *testing.T) {c,err:=Apply("",false,nil,"first");if err!=nil||c.Action!="create"||c.Content!="first" {t.Fatal("new file must not require original")}}
func TestOverlay(t *testing.T) {prev:=Change{Action:"create",Content:"first"};c,err:=Apply("",false,&prev,"first\nsecond");if err!=nil||c.Action!="create"||c.Content!="first\nsecond" {t.Fatal("overlay lost create/content")}}
func TestSummary(t *testing.T) {if Summary("  hello  ")!="hello"||FormatSummary(" hi ")!="Summary: hi" {t.Fatal("followup lost original behavior")}}
