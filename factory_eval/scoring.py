"""Fail-closed scoring of fixed requirements and authenticated CI evidence."""
from __future__ import annotations


def score(task, evidence, expected):
    errors = []
    for key in ("repository", "commit", "definition_sha", "run_id", "issuer", "workflow_run_id"):
        if evidence.get(key) != expected.get(key):
            errors.append(f"evidence identity mismatch: {key}")
    actual = evidence.get("jobs", [])
    names = [j.get("name") for j in actual]
    if len(names) != len(set(names)) or set(names) != set(task.data["required_jobs"]):
        errors.append("missing, duplicate or unexpected CI job")
    jobs = {j.get("name"): j for j in actual}
    reqs = {}
    for req in task.data["requirements"]:
        tests = evidence.get("tests", [])
        rows = [x for x in tests if x.get("name") in req["tests"]]
        covered = {x["name"] for x in rows}
        valid = (covered == set(req["tests"]) and len(rows) == len(covered)
                 and all(x.get("executed") is True and x.get("status") == "passed" for x in rows))
        reqs[req["id"]] = {"passed": bool(valid), "tests": req["tests"], "job": req["job"]}
    jobs_passed = all(jobs.get(n, {}).get("status") == "passed" and jobs[n].get("executed") is True
                      for n in task.data["required_jobs"])
    if not evidence.get("scope_valid", False):
        errors.append("forbidden or oversized changes")
    regression = evidence.get("regression_failed") is True
    passed = not errors and jobs_passed and all(x["passed"] for x in reqs.values())
    return {"status": "passed" if passed else "implementation_failed", "requirements": reqs,
            "errors": errors, "regression": regression, "jobs_passed": jobs_passed,
            "language_quality": "unmeasured" if task.id == "E01" else None}


def classify(attempts, *, stopped=None, completed=False):
    if stopped:
        return stopped
    if not attempts:
        return "blocked"
    return attempts[-1]["grading"]["status"]


def metrics(runs):
    import statistics
    scored = [r for r in runs if r.get("attempts")]
    completed = [a for r in scored for a in r["attempts"] if a.get("claimed_complete", r.get("completed", False))]
    def rate(n, d):
        return {"count": n, "denominator": d, "rate": n / d if d else None}
    requirements = {}
    for r in runs:
        for rid in r.get("requirement_ids", []):
            entry = requirements.setdefault(rid, {"count": 0, "denominator": 0})
            entry["denominator"] += 1
            attempts = r.get("attempts", [])
            if attempts and attempts[-1]["grading"]["requirements"].get(rid, {}).get("passed"):
                entry["count"] += 1
    durations = [r.get("usage", {}).get("elapsed_seconds", 0) for r in runs]
    return {"total": len(runs), "scored": len(scored),
            "duration_spread_seconds": {"min": min(durations, default=None), "max": max(durations, default=None),
                "population_stddev": statistics.pstdev(durations) if durations else None},
            "categories": {s: sum(r["status"] == s for r in runs) for s in (
                "passed", "implementation_failed", "blocked", "infrastructure_error", "budget_exceeded", "cancelled")},
            "final_all": rate(sum(r["status"] == "passed" for r in runs), len(runs)),
            "final_scored": rate(sum(r["status"] == "passed" for r in scored), len(scored)),
            "first_ci": rate(sum(r["attempts"][0]["grading"]["status"] == "passed" for r in scored), len(runs)),
            "regression": rate(sum(any(a["grading"]["regression"] for a in r["attempts"]) for r in scored), len(runs)),
            "false_completion": rate(sum(a["grading"]["status"] != "passed" for a in completed), len(completed)),
            "requirements": {k: rate(v["count"], v["denominator"]) for k, v in requirements.items()},
            "static_repairs": sum(r.get("static_repairs", 0) for r in runs),
            "ci_repairs": sum(r.get("ci_repairs_started", max(0, len(r.get("attempts", [])) - 1)) for r in runs),
            "usage": {key: sum(r.get("usage", {}).get(key, 0) for r in runs)
                      for key in ("calls", "input_tokens", "output_tokens", "elapsed_seconds", "ci_runs", "initial_ci_runs", "usage_unavailable_calls")}}
