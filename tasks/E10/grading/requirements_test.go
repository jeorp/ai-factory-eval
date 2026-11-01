package fixture
import "testing"
func TestIndependentChanges(t *testing.T) { b:=map[string]string{"a":"old-a","b":"old-b"}; l:=map[string]string{"a":"new-a","b":"old-b"}; r:=map[string]string{"a":"old-a","b":"new-b"}; got,err:=Merge(b,l,r); if err!=nil || got["a"]!="new-a" || got["b"]!="new-b" { t.Fatal(got,err) }; if l["b"]!="old-b" || r["a"]!="old-a" || b["a"]!="old-a" { t.Fatal("input mutated") } }
func TestConflict(t *testing.T) { b:=map[string]string{"a":"old"}; got,err:=Merge(b,map[string]string{"a":"left"},map[string]string{"a":"right"}); if err==nil || len(got)!=0 { t.Fatal(got,err) } }
func TestIdenticalChange(t *testing.T) { b:=map[string]string{"a":"old"}; got,err:=Merge(b,map[string]string{"a":"new"},map[string]string{"a":"new"}); if err!=nil || got["a"]!="new" { t.Fatal(got,err) } }
