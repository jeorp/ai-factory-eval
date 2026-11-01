package fixture
type Change struct { Action string; Content string }
func Apply(original string, exists bool, prev *Change, next string) (Change,error) {
 action:="update"; if !exists {action="create"}
 if prev!=nil && prev.Action=="create" {action="create"}
 return Change{Action:action,Content:next},nil
}
func Identity(s string) string {return s}
