from pathlib import Path
import math
import re

from .budget import Budget
from .task import Task
from .util import Invalid, read_json, repo, sha


def load_config(path, root):
    c = read_json(path)
    required = {"version", "evaluation_repository", "definition_sha", "workflow_ref", "core_repository", "core_path",
                "baseline_sha", "candidate_sha", "work_repositories", "tasks", "provider", "model", "model_version",
                "generation", "trials", "concurrency", "trial_limits", "total_limits", "pricing", "retention_days",
                "credentials", "check_issuer", "cleanup"}
    if set(c) != required or c["version"] != "factory-eval/v1":
        raise Invalid("configuration keys/version do not match factory-eval/v1")
    for k in ("evaluation_repository", "core_repository"):
        repo(c[k])
    for k in ("definition_sha", "baseline_sha", "candidate_sha"):
        sha(c[k])
    if not c["workflow_ref"] or c["workflow_ref"].startswith("-"):
        raise Invalid("a pinned workflow branch/tag is required")
    if c["concurrency"] != 1 or not isinstance(c["trials"], int) or not 1 <= c["trials"] <= 20:
        raise Invalid("serial execution and 1..20 trials required")
    if c["provider"] not in ("openai", "gemini") or not c["model"]:
        raise Invalid("provider and explicit model are required")
    if c["generation"] != {"mode": "production-defaults"}:
        raise Invalid("use the core provider's production generation settings")
    if not isinstance(c["retention_days"], int) or not 1 <= c["retention_days"] <= 90:
        raise Invalid("retention must be 1..90 days")
    if not isinstance(c["check_issuer"], int) or c["check_issuer"] <= 0:
        raise Invalid("trusted GitHub App ID required")
    if type(c["cleanup"]) is not bool:
        raise Invalid("cleanup must be boolean")
    if set(c["credentials"]) != {"model", "work", "evaluation"}:
        raise Invalid("credential environment references are required")
    for name in c["credentials"].values():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise Invalid("credentials must be environment variable names, never values")
    Budget(c["trial_limits"], c["pricing"])
    Budget(c["total_limits"], c["pricing"])
    if c["trial_limits"]["ci_attempts"] > 3:
        raise Invalid("at most two CI repairs (three generated-commit attempts) are allowed")
    if not c["tasks"] or len(c["tasks"]) != len(set(c["tasks"])):
        raise Invalid("select unique task IDs")
    if set(c["work_repositories"]) != set(c["tasks"]):
        raise Invalid("each selected task needs its own initial SHA and work repository")
    for tid in c["tasks"]:
        if tid not in ("E01", "E02", "E03", "E04"):
            raise Invalid("unknown task ID")
        Task.load(Path(root) / "tasks" / tid)
        work = c["work_repositories"][tid]
        if set(work) != {"repository", "initial_sha", "dedicated"} or work["dedicated"] is not True:
            raise Invalid("work repository must be explicitly marked dedicated")
        repo(work["repository"])
        sha(work["initial_sha"])
        if work["repository"].lower() in (c["evaluation_repository"].lower(), c["core_repository"].lower()):
            raise Invalid("evaluation/core repositories cannot be work targets")
    return c
