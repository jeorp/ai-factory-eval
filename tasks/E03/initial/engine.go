package fixture
import "errors"
type Change struct { Action string; Content string }
func Apply(original string, exists bool, prev *Change, next string) (Change,error) {
 if !exists { return Change{},errors.New("original required") }
 return Change{Action:"update",Content:next},nil
}
func Identity(s string) string {return s}
