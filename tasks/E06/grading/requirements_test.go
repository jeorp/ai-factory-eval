package fixture

import (
	"math/rand"
	"reflect"
	"testing"
)

func evalOrderCopy(prices []int) []int {
	if prices == nil {
		return nil
	}
	return append(make([]int, 0, len(prices)), prices...)
}

func TestDiscountedTotal(t *testing.T) {
	cases := []struct {
		prices         []int
		discount, want int
	}{
		{[]int{120, 80}, 30, 170}, {[]int{7, 3}, 2, 8},
		{[]int{0, 50, 0}, 50, 0}, {[]int{1, 1, 1, 1}, 1, 3},
	}
	for _, c := range cases {
		if got := Total(Order{c.prices, c.discount}); got != c.want {
			t.Fatalf("Total(%v, %d) = %d, want %d", c.prices, c.discount, got, c.want)
		}
	}
}

func TestNoDiscount(t *testing.T) {
	for _, prices := range [][]int{nil, {}, {0}, {10, 5}, {3, 0, 7, 0, 11}} {
		want := 0
		for _, p := range prices {
			want += p
		}
		if got := Total(Order{prices, 0}); got != want {
			t.Fatalf("no discount: %v got %d, want %d", prices, got, want)
		}
	}
}

func TestBoundaryTotals(t *testing.T) {
	max := int(^uint(0) >> 1)
	cases := []struct {
		order Order
		want  int
	}{
		{Order{nil, 0}, 0}, {Order{[]int{}, 0}, 0},
		{Order{[]int{0, 0}, 0}, 0}, {Order{[]int{123}, 123}, 0},
		{Order{[]int{max / 4, max / 4}, max / 8}, 2*(max/4) - max/8},
	}
	for _, c := range cases {
		if got := Total(c.order); got != c.want {
			t.Fatalf("boundary: got %d, want %d", got, c.want)
		}
	}
}

func TestOrderInputPreserved(t *testing.T) {
	for _, prices := range [][]int{nil, {}, {9, 3, 7}, {0, 10, 0}} {
		order := Order{prices, 0}
		if len(prices) > 0 {
			order.Discount = 2
		}
		before := evalOrderCopy(prices)
		first := Total(order)
		second := Total(order)
		if !reflect.DeepEqual(prices, before) || first != second {
			t.Fatal("input modified or repeated call changed result")
		}
	}
}

func TestTotalProperties(t *testing.T) {
	rng := rand.New(rand.NewSource(731))
	for i := 0; i < 200; i++ {
		prices := make([]int, 1+rng.Intn(24))
		sum := 0
		for j := range prices {
			prices[j] = rng.Intn(100000)
			sum += prices[j]
		}
		discount := rng.Intn(sum + 1)
		before := evalOrderCopy(prices)
		got := Total(Order{prices, discount})
		if got != sum-discount {
			t.Fatalf("case %d: got %d, want %d", i, got, sum-discount)
		}
		split := rng.Intn(len(prices) + 1)
		// Apply the discount only to the combined order; partition subtotals stay additive.
		parts := Total(Order{prices[:split], 0}) + Total(Order{prices[split:], 0}) - discount
		if got != parts {
			t.Fatalf("case %d: partition changed total", i)
		}
		reversed := evalOrderCopy(prices)
		for l, r := 0, len(reversed)-1; l < r; l, r = l+1, r-1 {
			reversed[l], reversed[r] = reversed[r], reversed[l]
		}
		if Total(Order{reversed, discount}) != got || !reflect.DeepEqual(prices, before) {
			t.Fatalf("case %d: order sensitivity or input mutation", i)
		}
	}
}
