package fixture
import "encoding/json"
type Request struct { Text string `json:"text"`; Language string `json:"language,omitempty"` }
type Session struct { Text string `json:"text"`; Language string `json:"language,omitempty"` }
type Call struct { Prompt string; Language string }
var Prompts = map[string]string{"chat":"Answer the user question in the specified output language.", "plan":"Plan the implementation in the specified output language.", "review":"Review the code in the specified output language.", "implement":"Implement the request in the specified output language.", "verify":"Verify changes in the specified output language."}
func Receive(body []byte) Session { var r Request; _ = json.Unmarshal(body, &r); return Session{Text:r.Text,Language:normalize(r.Language)} }
func Save(s Session) []byte { b,_:=json.Marshal(s);return b }
func Load(b []byte) Session {var s Session; _=json.Unmarshal(b,&s);return s}
func Calls(s Session) map[string]Call { out:=map[string]Call{}; for k,p:=range Prompts {out[k]=Call{Prompt:p,Language:normalize(s.Language)}};return out }
func Echo(s string) string { return s }

func normalize(s string) string {if s=="en" {return "en"};return "ja"}
