from dataclasses import dataclass, asdict
from decimal import Decimal
from datetime import date
import time

from .util import Invalid, BudgetExceeded, Cancelled


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reserved_cost: str = "0"
    estimated_cost: str = "0"
    ci_runs: int = 0
    initial_ci_runs: int = 0
    usage_unavailable_calls: int = 0


class Budget:
    def __init__(self, limits, pricing, *, clock=time.monotonic):
        required = {"calls", "input_tokens", "output_tokens", "seconds", "ci_attempts", "cost"}
        if set(limits) != required or any(isinstance(v, bool) or not Decimal(str(v)).is_finite() or Decimal(str(v)) <= 0 for v in limits.values()):
            raise Invalid("all positive budget limits are mandatory")
        for k in required - {"cost", "seconds"}:
            if not isinstance(limits[k], int):
                raise Invalid("count limits must be integers")
        if not isinstance(limits["seconds"], (int, float)):
            raise Invalid("time limit must be a number of seconds")
        if set(pricing) != {"input_per_million", "output_per_million", "currency", "effective_date"}:
            raise Invalid("pricing, currency and effective date are mandatory")
        if any(not Decimal(str(pricing[x])).is_finite() or Decimal(str(pricing[x])) < 0 for x in ("input_per_million", "output_per_million")):
            raise Invalid("negative pricing")
        if not pricing["currency"] or not isinstance(pricing["currency"], str) or len(pricing["currency"]) != 3:
            raise Invalid("ISO currency required")
        date.fromisoformat(pricing["effective_date"])
        self.limits, self.pricing, self.clock = limits, pricing, clock
        self.started, self.usage, self.cancelled = clock(), Usage(), False
        self.reserved_input = self.reserved_output = 0

    def cost(self, inputs, outputs):
        return (Decimal(str(self.pricing["input_per_million"])) * inputs
                + Decimal(str(self.pricing["output_per_million"])) * outputs) / 1_000_000

    def check(self):
        if self.cancelled:
            raise Cancelled("cancelled")
        if self.clock() - self.started >= self.limits["seconds"]:
            raise BudgetExceeded("time limit")

    def reserve(self, inputs, outputs):
        self.check()
        if min(inputs, outputs) < 0:
            raise Invalid("negative reservation")
        u = self.usage
        if (u.calls + 1 > self.limits["calls"]
                or self.reserved_input + inputs > self.limits["input_tokens"]
                or self.reserved_output + outputs > self.limits["output_tokens"]
                or Decimal(u.reserved_cost) + self.cost(inputs, outputs) > Decimal(str(self.limits["cost"]))):
            raise BudgetExceeded("next call cannot fit within budget")
        u.calls += 1
        self.reserved_input += inputs
        self.reserved_output += outputs
        u.reserved_cost = str(Decimal(u.reserved_cost) + self.cost(inputs, outputs))

    def record(self, inputs, outputs):
        if min(inputs, outputs) < 0:
            raise Invalid("invalid provider usage")
        self.usage.input_tokens += inputs
        self.usage.output_tokens += outputs
        self.usage.estimated_cost = str(self.cost(self.usage.input_tokens, self.usage.output_tokens))
        if (self.usage.input_tokens > self.limits["input_tokens"] or self.usage.output_tokens > self.limits["output_tokens"]
                or Decimal(self.usage.estimated_cost) > Decimal(str(self.limits["cost"]))):
            raise BudgetExceeded("provider usage exceeded reservation")

    def reserve_ci(self, *, initial=False):
        self.check()
        if not initial and self.usage.ci_runs - self.usage.initial_ci_runs >= self.limits["ci_attempts"]:
            raise BudgetExceeded("CI run limit")
        self.usage.ci_runs += 1
        self.usage.initial_ci_runs += int(initial)

    def snapshot(self):
        return {**asdict(self.usage), "elapsed_seconds": self.clock() - self.started,
                "currency": self.pricing["currency"], "provider_billed_cost": None}
