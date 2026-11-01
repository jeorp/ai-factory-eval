package fixture

type Order struct { Prices []int; Discount int }
func subtotal(prices []int) int { n:=0; for _, p:=range prices { n+=p }; return n }
func discounted(total, discount int) int { return total-discount }
func Total(o Order) int {
    total:=subtotal(o.Prices)
    if o.Discount != 0 { return discounted(total, o.Discount) }
    return total
}
