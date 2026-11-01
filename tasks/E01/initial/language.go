package fixture
import "encoding/json"
type Request struct { Text string `json:"text"`; Language string `json:"language,omitempty"` }
type Session struct { Text string `json:"text"`; Language string `json:"language,omitempty"` }
type Call struct { Prompt string; Language string }
var Prompts = map[string]string{"chat":"質問に回答してください", "plan":"実装を計画してください", "review":"レビューしてください", "implement":"実装してください", "verify":"変更を確認してください"}
func Receive(body []byte) Session { var r Request; _ = json.Unmarshal(body, &r); return Session{Text:r.Text} }
func Save(s Session) []byte { b,_:=json.Marshal(Session{Text:s.Text});return b }
func Load(b []byte) Session {var s Session; _=json.Unmarshal(b,&s);return s}
func Calls(s Session) map[string]Call { out:=map[string]Call{}; for k,p:=range Prompts {out[k]=Call{Prompt:p,Language:"ja"}};return out }
func Echo(s string) string { return s }
