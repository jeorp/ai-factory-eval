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
    gross_reserved_cost: str = "0"
    released_cost: str = "0"
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
        self.reservations = {}
        self.records = {}

    def cost(self, inputs, outputs):
        return (Decimal(str(self.pricing["input_per_million"])) * inputs
                + Decimal(str(self.pricing["output_per_million"])) * outputs) / 1_000_000

    def check(self):
        if self.cancelled:
            raise Cancelled("cancelled")
        if self.clock() - self.started >= self.limits["seconds"]:
            raise BudgetExceeded("time limit")

    @staticmethod
    def tokens(inputs, outputs):
        if any(type(n) is not int or n < 0 for n in (inputs, outputs)):
            raise Invalid("token counts must be nonnegative integers")

    def check_reservation(self, inputs, outputs, reservation_id=None):
        self.check()
        self.tokens(inputs, outputs)
        if reservation_id is not None and (not isinstance(reservation_id, str) or not reservation_id or reservation_id in self.reservations):
            raise Invalid("duplicate or invalid reservation ID")
        u = self.usage
        if (u.calls + 1 > self.limits["calls"]
                or self.reserved_input + inputs > self.limits["input_tokens"]
                or self.reserved_output + outputs > self.limits["output_tokens"]
                or Decimal(u.reserved_cost) + self.cost(inputs, outputs) > Decimal(str(self.limits["cost"]))):
            raise BudgetExceeded("next call cannot fit within budget")

    def reserve(self, inputs, outputs, reservation_id=None):
        self.check_reservation(inputs, outputs, reservation_id)
        u = self.usage
        u.calls += 1
        self.reserved_input += inputs
        self.reserved_output += outputs
        cost = self.cost(inputs, outputs)
        u.reserved_cost = str(Decimal(u.reserved_cost) + cost)
        u.gross_reserved_cost = str(Decimal(u.gross_reserved_cost) + cost)
        if reservation_id is not None:
            self.reservations[reservation_id] = (inputs, outputs)

    def record(self, inputs, outputs, *, usage_id=None, reservation_id=None, settle=False):
        self.tokens(inputs, outputs)
        value = (inputs, outputs, reservation_id, settle)
        if usage_id is not None:
            if not isinstance(usage_id, str) or not usage_id:
                raise Invalid("invalid usage ID")
            if usage_id in self.records:
                if self.records[usage_id] != value:
                    raise Invalid("conflicting provider usage")
                return False
        reserved = None
        if settle:
            if usage_id is None or reservation_id not in self.reservations or self.reservations[reservation_id] is None:
                raise Invalid("settlement requires an unsettled reservation and unique usage ID")
            reserved = self.reservations[reservation_id]
        if usage_id is not None:
            self.records[usage_id] = value
        self.usage.input_tokens += inputs
        self.usage.output_tokens += outputs
        self.usage.estimated_cost = str(self.cost(self.usage.input_tokens, self.usage.output_tokens))
        overflow = False
        if reserved is not None:
            ri, ro = reserved
            # Never release credit on a violated upper bound. Charge any excess
            # and stop; malformed/duplicate settlements cannot mint credit.
            overflow = inputs > ri or outputs > ro
            di, do = (max(inputs, ri) - ri, max(outputs, ro) - ro) if overflow else (inputs - ri, outputs - ro)
            self.reserved_input += di
            self.reserved_output += do
            delta = self.cost(di, do)
            self.usage.reserved_cost = str(Decimal(self.usage.reserved_cost) + delta)
            if not overflow:
                self.usage.released_cost = str(Decimal(self.usage.released_cost) - delta)
            self.reservations[reservation_id] = None
        if (overflow or self.usage.input_tokens > self.limits["input_tokens"] or self.usage.output_tokens > self.limits["output_tokens"]
                or Decimal(self.usage.estimated_cost) > Decimal(str(self.limits["cost"]))):
            raise BudgetExceeded("provider usage exceeded reservation")
        return True

    def check_ci(self, *, initial=False, include_initial=False):
        self.check()
        used = self.usage.ci_runs if include_initial else self.usage.ci_runs - self.usage.initial_ci_runs
        if (include_initial or not initial) and used >= self.limits["ci_attempts"]:
            raise BudgetExceeded("CI run limit")

    def reserve_ci(self, *, initial=False, include_initial=False):
        self.check_ci(initial=initial, include_initial=include_initial)
        self.usage.ci_runs += 1
        self.usage.initial_ci_runs += int(initial)

    def snapshot(self):
        return {**asdict(self.usage), "elapsed_seconds": self.clock() - self.started,
                "currency": self.pricing["currency"], "provider_billed_cost": None}
