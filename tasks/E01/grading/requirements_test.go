package fixture
import ("testing";"encoding/json";"strings";"reflect")
var _ = json.Marshal
var _ = strings.Contains
var _ = reflect.TypeOf
func TestPrompts(t *testing.T) { for _,k:=range []string{"chat","plan","review","implement","verify"} {p:=Prompts[k];if len(p)<10 {t.Errorf("missing prompt: %s",k)};for _,r:=range p {if r>127 {t.Errorf("non-English prompt: %s",k);break}}} }
func TestReceive(t *testing.T) {for _,lang:=range []string{"en","ja"} {s:=Receive([]byte(`{"text":"hello","language":"`+lang+`"}`));if s.Language!=lang {t.Errorf("API lost language: %s",lang)}}}
func TestPersistence(t *testing.T) {for _,lang:=range []string{"en","ja"} {s:=Load(Save(Session{Text:"hello",Language:lang}));if s.Language!=lang||s.Text!="hello" {t.Errorf("roundtrip: %s",lang)}}}
func TestCalls(t *testing.T) {for _,lang:=range []string{"en","ja"} {calls:=Calls(Session{Language:lang});for _,k:=range []string{"chat","plan","review","implement","verify"} {if calls[k].Language!=lang||calls[k].Prompt!=Prompts[k] {t.Errorf("call %s lost %s",k,lang)}}}}
