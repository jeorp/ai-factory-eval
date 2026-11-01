package fixture
import "errors"
func Merge(base, local, remote map[string]string) (map[string]string,error) {
 result:=map[string]string{}
 for k,b:=range base {
  l,r:=local[k],remote[k]
  if l!=b && r!=b && l!=r { return nil,errors.New("conflict") }
  if l!=b { result[k]=l } else { result[k]=r }
 }
 return result,nil
}
