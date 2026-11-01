package fixture
func Merge(base, local, remote map[string]string) (map[string]string,error) {
 result:=map[string]string{}; for k,v:=range local { result[k]=v }; return result,nil
}
