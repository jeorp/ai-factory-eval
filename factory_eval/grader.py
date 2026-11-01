"""Trusted grader. Execute candidate code only in the credential-free grading container.

The self-test command may execute the repository's reviewed fixtures locally.
Never call evaluate() on generated code on a developer machine.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .task import Task
from .util import Invalid, digest, clean_env, write_json


def inventory(root):
    root = Path(root)
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        if p.is_symlink():
            raise Invalid("symlink rejected in grading tree")
        if p.is_file():
            out[rel] = digest(p.read_bytes())
    return out


def scope(task, candidate):
    original, actual = inventory(task.root / "initial"), inventory(candidate)
    changed = [p for p in set(original) | set(actual) if original.get(p) != actual.get(p)]
    return (len(changed) <= task.data["max_files"]
            and all(task.permits(p) for p in changed)
            and sum((Path(candidate) / p).stat().st_size for p in changed if p in actual) <= task.data["max_bytes"])


def prepare(task, candidate, destination):
    """Stage before launching untrusted code, then mount destination read-only."""
    inventory(candidate)
    dest = Path(destination)
    shutil.copytree(candidate, dest, ignore=shutil.ignore_patterns(".git"))
    for kind in ("requirements", "regression"):
        shutil.copyfile(task.root / "grading" / f"{kind}_test.go", dest / f"zzeval_{kind}_test.go")
    return dest


def run(args, cwd):
    env = clean_env()
    env.update({"GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off", "CGO_ENABLED": "0"})
    try:
        p = subprocess.run(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=90)
        return p.returncode, p.stdout[-200_000:]
    except subprocess.TimeoutExpired:
        return 124, "execution timeout; evidence incomplete"


def go_tests(work, names, runner=run):
    code, log = runner(["go", "test", "-json", "-count=1", "-timeout=45s", "-run", "^(" + "|".join(names) + ")$", "."], work)
    terminal = {name: [] for name in names}
    starts = {name: 0 for name in names}
    for line in log.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        name = row.get("Test")
        if name in terminal:
            if row.get("Action") == "run":
                starts[name] += 1
            if row.get("Action") in ("pass", "fail", "skip"):
                terminal[name].append(row["Action"])
    results = [{"name": n, "executed": starts[n] == 1 and len(terminal[n]) == 1,
                "status": "passed" if terminal[n] == ["pass"] and starts[n] == 1 else "failed"}
               for n in names]
    if code and all(r["status"] == "passed" for r in results):
        results.append({"name": "__process_exit", "executed": True, "status": "failed"})
    return results, log


def trace_valid(trace):
    try:
        before, after = trace["before_followup"], trace["after_followup"]
        return (before["action"] == after["action"] == "create"
                and re.search(r"\bfunc\s+Summary\s*\(", before["content"]) is not None
                and re.search(r"\bfunc\s+Summary\s*\(", after["content"]) is not None
                and "FormatSummary" not in before["content"]
                and re.search(r"\bfunc\s+FormatSummary\s*\(", after["content"]) is not None
                and before["content"] != after["content"]
                and before["base_commit"] == after["base_commit"]
                and before["base_exists"] is False and after["base_exists"] is False
                and before.get("published") is False and after.get("published") is False)
    except (KeyError, TypeError):
        return False


def execute(task, work, *, scope_valid, trace=None, runner=run):
    """work contains fixed injected tests and must be a read-only mount in live runs."""
    code, build_log = runner(["go", "build", "./..."], work)
    names = [n for r in task.data["requirements"] for n in r["tests"] if n not in ("TestUI", "TestTrace")]
    requirements, req_log = go_tests(work, names, runner)
    if task.id == "E01":
        ui_code, ui_log = runner(["node", "--experimental-strip-types", str(task.root / "grading" / "ui.mjs"), str(Path(work).resolve() / "ui.ts")], work)
        requirements.append({"name": "TestUI", "executed": True, "status": "passed" if ui_code == 0 else "failed"})
        req_log += "\n" + ui_log
    if task.id == "E03":
        requirements.append({"name": "TestTrace", "executed": trace is not None,
                             "status": "passed" if trace_valid(trace) else "failed"})
    regressions, reg_log = go_tests(work, ["TestRegression"], runner)
    regression_failed = any(r["status"] != "passed" for r in regressions)
    # Existing public tests are also mandatory. Deletion/modification is a scope failure.
    public = []
    for p in (task.root / "initial").glob("*_test.go"):
        public += re.findall(r"func (Test\w+)\(", p.read_text())
    if public:
        rows, log = go_tests(work, public, runner)
        regressions += rows
        reg_log += "\n" + log
    def passed(rows):
        return bool(rows) and all(r["executed"] and r["status"] == "passed" for r in rows)
    return {"scope_valid": scope_valid, "regression_failed": regression_failed,
            "jobs": [{"name": "build", "executed": True, "status": "passed" if code == 0 else "failed"},
                     {"name": "requirements", "executed": True, "status": "passed" if passed(requirements) else "failed"},
                     {"name": "regression", "executed": True, "status": "passed" if passed(regressions) else "failed"}],
            "tests": requirements + regressions,
            "failure_logs": {"build": build_log, "requirements": req_log, "regression": reg_log},
            "logs_complete": all(len(x) < 190_000 and "evidence incomplete" not in x for x in (build_log, req_log, reg_log))}


def reference_trace(task):
    content = (task.root / "reference" / "summary.go").read_text()
    return {"before_followup": {"action": "create", "content": content.split("func FormatSummary")[0],
                               "base_commit": "0" * 40, "base_exists": False, "published": False},
            "after_followup": {"action": "create", "content": content, "base_commit": "0" * 40, "base_exists": False, "published": False}}


def evaluate(task, candidate, trace=None):
    with tempfile.TemporaryDirectory(prefix="factory-selftest-") as temp:
        work = prepare(task, candidate, Path(temp) / "work")
        return execute(task, work, scope_valid=scope(task, candidate), trace=trace)


def container_runner(image, task, work):
    """No credentials/env, network, Docker socket or writable grading source in the sandbox.

    Only raw process output crosses the boundary. The trusted host writes grading.json.
    """
    work = Path(work).resolve()
    grading = task.root / "grading"
    def runner(args, cwd):
        args = [x.replace(str(work), "/work").replace(str(grading), "/grading") for x in args]
        docker = ["docker", "run", "--rm", "--network=none", "--read-only", "--cap-drop=ALL",
                  "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=2g", "--cpus=2",
                  "--user=65534:65534", "--tmpfs=/tmp:rw,nosuid,exec,size=1073741824,mode=1777",
                  "-e", "HOME=/tmp", "-e", "GOCACHE=/tmp/go-build", "-e", "GOPATH=/tmp/go",
                  "-e", "GOPROXY=off", "-e", "GOSUMDB=off", "-e", "GOTOOLCHAIN=local", "-e", "CGO_ENABLED=0",
                  "-v", f"{work}:/work:ro", "-v", f"{grading}:/grading:ro", "-w", "/work", image, *args]
        code, log = run(docker, cwd)
        if code in (125, 126, 127):
            raise Invalid("grading container could not start its command")
        return code, log
    return runner


def self_test(tasks):
    from .scoring import score
    results = []
    identity = {"repository": "test/fixture", "commit": "0" * 40, "definition_sha": "1" * 40,
                "run_id": "selftest", "issuer": 1, "workflow_run_id": 1}
    for path in sorted(Path(tasks).iterdir()):
        if not (path / "task.json").is_file():
            continue
        task = Task.load(path)
        for variant in ("initial", "reference"):
            trace = reference_trace(task) if task.id == "E03" and variant == "reference" else None
            evidence = evaluate(task, task.root / variant, trace)
            graded = score(task, {**evidence, **identity}, identity)
            observed = {k: v["passed"] for k, v in graded["requirements"].items()}
            if observed != task.data[variant] or (variant == "reference" and graded["status"] != "passed"):
                raise Invalid(f"{task.id} {variant}: unexpected grading {observed}; {evidence['failure_logs']}")
            results.append(f"{task.id}/{variant}: OK")
        for mutation in task.data["mutations"]:
            with tempfile.TemporaryDirectory(prefix="factory-mutation-") as temp:
                candidate = Path(temp) / "candidate"
                shutil.copytree(task.root / "reference", candidate)
                f = candidate / mutation["file"]
                s = f.read_text()
                if mutation["old"] not in s:
                    raise Invalid("stale mutation")
                s = s.replace(mutation["old"], mutation["new"])
                if mutation.get("extra_import"):
                    s = s.replace("package fixture", 'package fixture\nimport "' + mutation["extra_import"] + '"')
                f.write_text(s)
                trace = reference_trace(task) if task.id == "E03" else None
                evidence = evaluate(task, candidate, trace)
                graded = score(task, {**evidence, **identity}, identity)
                if graded["status"] == "passed" or any(graded["requirements"][k]["passed"] for k in mutation.get("detect", [])):
                    raise Invalid(f"{task.id} mutation escaped: {mutation['name']}")
                if mutation.get("detect_scope") and evidence["scope_valid"]:
                    raise Invalid("test tampering escaped scope check")
                results.append(f"{task.id}/{mutation['name']}: OK")
    return results
