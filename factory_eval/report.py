from pathlib import Path
import json
from .scoring import metrics
from .util import write_json


def report(runs, destination):
    destination = Path(destination)
    groups = {name: metrics([r for r in runs if r["variant"] == name]) for name in ("baseline", "candidate")}
    conditions = set()
    task_versions = {}
    for r in runs:
        c = r.get("config", {})
        conditions.add(json.dumps({k: c.get(k) for k in ("definition_sha", "provider", "model", "model_version", "generation",
            "trial_limits", "total_limits", "pricing", "work_repositories")}, sort_keys=True))
        task_versions.setdefault(r["task_id"], set()).add((r.get("task_version"), r.get("task_fingerprint")))
    compatible = len(conditions) <= 1 and all(len(v) == 1 for v in task_versions.values())
    groups["comparison_valid"] = compatible
    groups["by_task"] = {tid: {name: metrics([r for r in runs if r["task_id"] == tid and r["variant"] == name])
                                  for name in ("baseline", "candidate")} for tid in sorted(task_versions)}
    write_json(destination / "comparison.json", groups)
    text = ["# AI Factory evaluation", "", "Small fixed task suite; counts and denominators are reported. Model version may be unknown.",
            "Comparison conditions: " + ("consistent." if compatible else "MISMATCH — rates below are diagnostic only; do not treat these as a controlled comparison."),
            "Language fluency/quality is not measured by E01.", "", "| Variant | Passed / all | Passed / scored | First CI | False completion |", "|---|---:|---:|---:|---:|"]
    def fmt(v):
        return f'{v["count"]}/{v["denominator"]}'
    for name, m in groups.items():
        if name not in ("baseline", "candidate"):
            continue
        text.append(f'| {name} | {fmt(m["final_all"])} | {fmt(m["final_scored"])} | {fmt(m["first_ci"])} | {fmt(m["false_completion"])} |')
    text += ["", "| Run | Task | Variant | Core SHA | Result | Last grade | CI attempts | CI repairs started | Reason |", "|---|---|---|---|---|---|---:|---:|---|"]
    for r in runs:
        reason = str(r.get("reason", "")).replace("|", "\\|").replace("\n", " ")
        attempts = r.get("attempts", [])
        grade = attempts[-1]["grading"]["status"] if attempts else "unscored"
        repairs = r.get("ci_repairs_started", max(0, len(attempts)-1))
        text.append(f'| {r["run_id"]} | {r["task_id"]} | {r["variant"]} | `{r["core_sha"]}` | {r["status"]} | {grade} | {len(attempts)} | {repairs} | {reason} |')
    text += ["", "## Per-trial observations", ""]
    for r in runs:
        text += [f'### {r["run_id"]}', "", f'Task fingerprint: `{r.get("task_fingerprint", "unknown")}`',
                 f'Usage: `{r.get("usage", {})}`', f'Static repairs: {r.get("static_repairs", 0)}; CI repairs started: {r.get("ci_repairs_started", max(0, len(r.get("attempts", []))-1))}.',
                 f'Unverified: {r.get("unverified", [])}', ""]
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "report.md").write_text("\n".join(text) + "\n")
    return groups
