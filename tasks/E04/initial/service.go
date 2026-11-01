package fixture
import "strings"
func Add(a,b int) int {return a-b}
func Valid(s string) bool {return len(s)>0}
func Normalize(s string) string {return s}
func Lookup(m map[string]string,k string) string {if m==nil {return "missing"};return m[k]}
func Version() string {return "v1"}
var _ = strings.TrimSpace
