package fixture
import "encoding/json"
type Item struct { ID string `json:"id"`; Name string `json:"name"`; Label string `json:"label,omitempty"` }
type View struct { ID string `json:"id"`; Name string `json:"name"`; Label string `json:"label,omitempty"` }
func Convert(i Item) View { return View{ID:i.ID,Name:i.Name,Label:i.Label} }
func Save(i Item) []byte {b,_:=json.Marshal(i);return b}
func Load(b []byte) Item {var i Item;_=json.Unmarshal(b,&i);return i}
func Legacy() Item {return Item{ID:"1",Name:"legacy"}}
