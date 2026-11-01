package fixture
import "strings"
func Summary(s string) string {return strings.TrimSpace(s)}
func FormatSummary(s string) string {return "Summary: "+Summary(s)}
