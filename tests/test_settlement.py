import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factory_eval.budget import Budget
from factory_eval.orchestrator import Evaluation
from factory_eval.util import Invalid, BudgetExceeded
import test_adapter as contract


def budget(**limits):
    return Budget({"calls": 20, "input_tokens": 1000, "output_tokens": 1000,
                   "seconds": 60, "ci_attempts": 3, "cost": "0.001", **limits},
                  {"input_per_million": "1", "output_per_million": "2",
                   "currency": "USD", "effective_date": "2026-11-01"})


class SettlementTests(unittest.TestCase):
    def test_known_usage_releases_only_unused_reservation(self):
        b = budget()
        for i in range(10):
            b.reserve(100, 100, str(i))
            b.record(10, 5, usage_id=str(i), reservation_id=str(i), settle=True)
        self.assertEqual(b.reserved_input, 100)
        self.assertEqual(b.reserved_output, 50)
        self.assertEqual(b.snapshot()["reserved_cost"], "0.00020")
        self.assertEqual(b.snapshot()["gross_reserved_cost"], "0.0030")
        self.assertEqual(b.snapshot()["released_cost"], "0.00280")
        self.assertEqual(b.snapshot()["calls"], 10)

    def test_unknown_errors_and_retries_keep_reservations(self):
        b = budget()
        for i in range(3):
            b.reserve(100, 100, str(i))
            b.record(0, 0, usage_id=str(i))
        with self.assertRaises(BudgetExceeded):
            b.reserve(100, 100, "4")
        self.assertEqual(b.snapshot()["reserved_cost"], "0.0009")
        self.assertEqual(b.snapshot()["released_cost"], "0")

    def test_duplicate_conflicting_and_foreign_settlement(self):
        b = budget()
        b.reserve(100, 100, "a")
        kwargs = dict(usage_id="u", reservation_id="a", settle=True)
        b.record(10, 5, **kwargs)
        before = b.snapshot()
        self.assertFalse(b.record(10, 5, **kwargs))
        for args in [dict(usage_id="u", reservation_id="a", settle=True),
                     dict(usage_id="other", reservation_id="a", settle=True),
                     dict(usage_id="foreign", reservation_id="missing", settle=True)]:
            with self.assertRaises(Invalid):
                b.record(11, 5, **args)
        self.assertEqual(b.usage.calls, before["calls"])
        self.assertEqual(b.usage.reserved_cost, before["reserved_cost"])
        self.assertEqual(b.usage.input_tokens, before["input_tokens"])
        with self.assertRaises(Invalid):
            b.reserve(100, 100, "a")

    def test_upper_bound_violation_stops_without_refunding(self):
        b = budget()
        b.reserve(100, 100, "a")
        with self.assertRaises(BudgetExceeded):
            b.record(101, 5, usage_id="u", reservation_id="a", settle=True)
        self.assertEqual(b.usage.released_cost, "0")
        self.assertGreaterEqual(float(b.usage.reserved_cost), .0003)
        self.assertEqual(b.usage.input_tokens, 101)

    def test_bad_token_counts_cannot_create_credit(self):
        b = budget()
        for value in [-1, 1.5, True, "1"]:
            with self.assertRaises(Invalid):
                b.reserve(value, 1)
            with self.assertRaises(Invalid):
                b.record(value, 1)
        self.assertEqual(b.usage.calls, 0)

    def test_call_limit_is_not_refunded(self):
        b = budget(calls=1)
        b.reserve(100, 100, "a")
        b.record(10, 5, usage_id="u", reservation_id="a", settle=True)
        with self.assertRaises(BudgetExceeded):
            b.reserve(1, 1, "b")

    def test_existing_destination_cannot_restart_with_fresh_budget(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            e = Evaluation(root / "examples/config.json", root, temp)
            with patch.object(e, "preflight") as preflight:
                with self.assertRaises(FileExistsError):
                    e.run()
                preflight.assert_not_called()


# Reuse the contract fixture, while testing new event sequences against a real
# child process. The fixture emits no network requests or model generations.
class SettlementAdapterTests(contract.AdapterTests):
    def setUp(self):
        super().setUp()
        self.adapter.close()
        from factory_eval.adapter import Adapter
        script = contract.SCRIPT.replace("if command['action']=='write':", """
 if command['action']=='settled':
  emit('reserve',reservation_id='r1',input_tokens=20,output_tokens=10)
  json.loads(sys.stdin.readline())
  emit('usage',usage_id='u1',reservation_id='r1',input_tokens=5,output_tokens=3,usage_known=True,settle=True)
  emit('usage',usage_id='u1',reservation_id='r1',input_tokens=5,output_tokens=3,usage_known=True,settle=True)
  emit('result',implementation={'status':'ready'})
 if command['action']=='unknown':
  emit('reserve',reservation_id='r1',input_tokens=20,output_tokens=10)
  json.loads(sys.stdin.readline())
  emit('usage',usage_id='u1',input_tokens=0,output_tokens=0,usage_known=False,settle=False)
  emit('usage',usage_id='u1',input_tokens=0,output_tokens=0,usage_known=False,settle=False)
  emit('result',implementation={'status':'ready'})
 if command['action']=='write':""")
        self.binary.write_text(script)
        self.adapter = Adapter(self.binary, {}, self.trial, self.total, self.events.append)

    def test_settlement_is_identical_in_trial_and_total(self):
        self.adapter.call('settled')
        for b in (self.trial, self.total):
            self.assertEqual(b.usage.input_tokens, 5)
            self.assertEqual(b.reserved_input, 5)
            self.assertEqual(b.reserved_output, 3)
            self.assertEqual(b.usage.calls, 1)
        self.assertEqual(len([e for e in self.events if e['type'] == 'usage']), 1)

    def test_unknown_usage_stays_reserved_once(self):
        self.adapter.call('unknown')
        for b in (self.trial, self.total):
            self.assertEqual(b.reserved_input, 20)
            self.assertEqual(b.reserved_output, 10)
            self.assertEqual(b.usage.usage_unavailable_calls, 1)

    def test_trial_rejection_does_not_charge_total(self):
        self.trial.limits['input_tokens'] = 1
        with self.assertRaises(BudgetExceeded):
            self.adapter.call('settled')
        self.assertEqual(self.total.usage.calls, 0)
        self.assertEqual(self.trial.usage.calls, 0)

    def test_reservation_is_saved_before_acknowledgment(self):
        def fail_event(event):
            raise OSError('event storage unavailable')
        self.adapter.event = fail_event
        with self.assertRaises(OSError):
            self.adapter.call('settled')
        self.assertEqual(self.total.usage.calls, 1)
        self.assertEqual(self.total.usage.input_tokens, 0)

    def test_total_ids_do_not_collide_between_trials(self):
        from factory_eval.adapter import Adapter
        self.adapter.call('settled')
        other_trial = budget()
        other = Adapter(self.binary, {}, other_trial, self.total, self.events.append)
        try:
            other.call('settled')
        finally:
            other.close()
        self.assertEqual(self.total.usage.calls, 2)
        self.assertEqual(self.total.usage.input_tokens, 10)
        self.assertEqual(self.total.reserved_input, 10)
