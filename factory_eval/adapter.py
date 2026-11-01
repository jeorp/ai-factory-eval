"""Build the headless entrypoint *inside* a pinned core checkout; speak JSONL."""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tarfile
import tempfile
import time

from . import PROTOCOL_VERSION
from .util import Invalid, Blocked, BudgetExceeded, command, clean_env, sha


def build_core(source, commit, integration, destination):
    sha(commit)
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if command(["git", "rev-parse", f"{commit}^{{commit}}"], cwd=source) != commit:
        raise Invalid("core commit unavailable")
    destination.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryFile() as archive:
        p = subprocess.run(["git", "archive", "--format=tar", commit], cwd=source, stdout=archive, check=False)
        if p.returncode:
            raise Invalid("unable to archive pinned core")
        archive.seek(0)
        with tarfile.open(fileobj=archive) as tar:
            tar.extractall(destination, filter="data")
    target = destination / "backend" / "cmd" / "factory-eval"
    if target.exists():
        raise Invalid("core already owns cmd/factory-eval; use its native versioned adapter instead")
    target.mkdir(parents=True)
    shutil.copyfile(Path(integration) / "main.go", target / "main.go")
    shutil.copyfile(Path(integration) / "main_test.go", target / "main_test.go")
    has_ci = any("func StartCIFix(" in p.read_text() for p in (destination / "backend/internal/agent").glob("*.go") if not p.name.endswith("_test.go"))
    compat = "compat_ci.go.txt" if has_ci else "compat_legacy.go.txt"
    shutil.copyfile(Path(integration) / compat, target / "compat.go")
    binary = destination / "factory-eval-core"
    command(["go", "build", "-trimpath", "-o", str(binary), "./cmd/factory-eval"],
            cwd=destination / "backend", timeout=300)
    return binary


class Adapter:
    def __init__(self, binary, env, trial_budget, total_budget, event):
        self.trial, self.total, self.event = trial_budget, total_budget, event
        self.p = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env={**clean_env(), **env}, bufsize=0,
                                  start_new_session=True)
        self.buffer = b""
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.p.stdout, selectors.EVENT_READ)

    def send(self, value):
        self.trial.check()
        self.total.check()
        self.p.stdin.write((json.dumps(value) + "\n").encode())
        self.p.stdin.flush()

    def row(self):
        while b"\n" not in self.buffer:
            self.trial.check()
            self.total.check()
            if not self.selector.select(timeout=0.25):
                if self.p.poll() is not None:
                    raise Invalid("headless adapter exited unexpectedly")
                continue
            chunk = os.read(self.p.stdout.fileno(), 65536)
            if not chunk:
                raise Invalid("headless adapter output closed")
            self.buffer += chunk
            if len(self.buffer) > 4_000_000:
                raise Invalid("adapter event size limit")
        line, self.buffer = self.buffer.split(b"\n", 1)
        try:
            return json.loads(line)
        except ValueError:
            raise Invalid("invalid adapter JSONL") from None

    def call(self, action, **payload):
        self.send({"version": PROTOCOL_VERSION, "action": action, **payload})
        while True:
            r = self.row()
            kind = r.get("type")
            if r.get("version") != PROTOCOL_VERSION:
                raise Invalid("adapter protocol mismatch")
            if kind == "reserve":
                # Charge every physical HTTP attempt, including provider errors/retries.
                self.total.reserve(r["input_tokens"], r["output_tokens"])
                self.trial.reserve(r["input_tokens"], r["output_tokens"])
                self.send({"allow": True})
                self.event({"type": "model_reservation", "input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"]})
            elif kind == "usage":
                if not r.get("usage_known", True):
                    self.total.usage.usage_unavailable_calls += 1
                    self.trial.usage.usage_unavailable_calls += 1
                self.total.record(r["input_tokens"], r["output_tokens"])
                self.trial.record(r["input_tokens"], r["output_tokens"])
                self.event(r)
            elif kind == "checkpoint":
                self.event(r)
            elif kind == "write_intent":
                self.trial.check()
                self.total.check()
                self.send({"allow": True})
                self.event({"type": "github_write_authorized"})
            elif kind == "result":
                if r.get("error"):
                    if r["error"] in ("scope_rejected", "clarification_required", "ci_capability_unavailable", "credential_echo_blocked") or r["error"].startswith(("implementation_", "invalid_plan")):
                        raise Blocked(r["error"])
                    raise Invalid("headless core: " + r["error"])
                return r
            else:
                raise Invalid("unknown adapter event")

    def close(self):
        import signal
        if self.p.poll() is None:
            os.killpg(self.p.pid, signal.SIGTERM)
            try:
                self.p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.p.pid, signal.SIGKILL)
                self.p.wait()
        self.selector.close()
        self.p.stdin.close()
        self.p.stdout.close()
