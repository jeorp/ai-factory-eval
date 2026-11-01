from __future__ import annotations

import json
import difflib
import os
from pathlib import Path
import secrets
import signal
import tempfile
import time

from .adapter import Adapter, build_core
from .budget import Budget
from .config import load_config
from .github import GitHub
from .grader import inventory
from .report import report
from .scoring import score, classify
from .task import Task
from .util import EvalError, Invalid, Blocked, ImplementationFailed, BudgetExceeded, Cancelled, command, digest, now, redact, write_json


def schedule(c):
    return [(tid, trial, variant, c[f"{variant}_sha"]) for tid in c["tasks"]
            for trial in range(c["trials"]) for variant in ("baseline", "candidate")]


def validate_initial(api, task, repository, commit):
    import hashlib
    tree = api.request("GET", f"/repos/{repository}/git/trees/{commit}?recursive=1")
    if tree.get("truncated"):
        raise Invalid("initial tree listing incomplete")
    actual = {}
    for entry in tree["tree"]:
        if entry["type"] == "tree":
            continue
        if entry["mode"] not in ("100644", "100755") or entry["type"] != "blob":
            raise Invalid("initial symlink/submodule rejected")
        actual[entry["path"]] = entry["sha"]
    expected = {}
    for p in (task.root / "initial").rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            expected[p.relative_to(task.root / "initial").as_posix()] = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if actual != expected:
        raise Invalid(f"{task.id}: initial commit differs from the fixed synthetic fixture")


class Evaluation:
    def __init__(self, config, root, destination):
        self.root, self.destination = Path(root).resolve(), Path(destination).resolve()
        self.config = load_config(config, self.root)
        self.total = Budget(self.config["total_limits"], self.config["pricing"])
        self.runs = []
        self.secrets = tuple(os.environ.get(x, "") for x in self.config["credentials"].values())
        self.work_api = GitHub(os.environ.get(self.config["credentials"]["work"], ""))
        self.eval_api = GitHub(os.environ.get(self.config["credentials"]["evaluation"], ""))

    def preflight(self):
        c = self.config
        if not all(self.secrets):
            raise Invalid("credential environment variables are unavailable")
        if command(["git", "rev-parse", "HEAD"], cwd=self.root) != c["definition_sha"]:
            raise Invalid("evaluation checkout does not match pinned definition SHA")
        if command(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=self.root):
            raise Invalid("evaluation checkout must be clean")
        if self.eval_api.resolve(c["evaluation_repository"], c["workflow_ref"]) != c["definition_sha"]:
            raise Invalid("workflow ref is not pinned to definition SHA")
        accessible = self.eval_api.pages("/installation/repositories", "repositories")
        if c["evaluation_repository"].lower() not in {r["full_name"].lower() for r in accessible}:
            raise Invalid("evaluation installation token cannot access the definition repository")
        for tid in c["tasks"]:
            task = Task.load(self.root / "tasks" / tid)
            work = c["work_repositories"][tid]
            self.work_api.verify_repository(work["repository"])
            if self.work_api.resolve(work["repository"], work["initial_sha"]) != work["initial_sha"]:
                raise Invalid("initial commit unavailable")
            validate_initial(self.work_api, task, work["repository"], work["initial_sha"])

    def grade(self, task, record, commit, base, trial, phase="implementation", trace=None):
        c = self.config
        trial.check()
        self.total.check()
        initial = phase == "initial"
        self.total.check_ci(initial=initial, include_initial=True)
        trial.check_ci(initial=initial)
        self.total.reserve_ci(initial=initial, include_initial=True)
        trial.reserve_ci(initial=initial)
        suffix = f'{record["run_id"]}-{phase}-{len(record["attempts"])}'
        inputs = {"run_id": suffix, "repository": record["repository"], "head_sha": commit, "base_sha": base,
                  "task_id": task.id, "definition_sha": c["definition_sha"],
                  "trace": json.dumps(trace or {}, separators=(",", ":")), "retention_days": str(c["retention_days"])}
        self.eval_api.dispatch(c["evaluation_repository"], c["workflow_ref"], c["definition_sha"], inputs)
        workflow = None
        while True:
            trial.check()
            self.total.check()
            if workflow is None:
                workflow = self.eval_api.find_run(c["evaluation_repository"], suffix, c["definition_sha"])
            else:
                workflow = self.eval_api.request("GET", f'/repos/{c["evaluation_repository"]}/actions/runs/{workflow["id"]}')
            if workflow and workflow["status"] == "completed":
                if workflow["conclusion"] != "success":
                    raise Invalid("trusted grading workflow failed (not an implementation grade)")
                break
            time.sleep(3)
        evidence = self.eval_api.evidence(c["evaluation_repository"], workflow, "grading-" + suffix)
        expected = {"repository": record["repository"], "commit": commit, "definition_sha": c["definition_sha"],
                    "run_id": suffix, "issuer": c["check_issuer"], "workflow_run_id": workflow["id"]}
        checks = self.work_api.pages(f'/repos/{record["repository"]}/commits/{commit}/check-runs', "check_runs")
        required = {"factory-eval/" + n for n in task.data["required_jobs"]}
        verified = [x for x in checks if x.get("name") in required and x.get("app", {}).get("id") == c["check_issuer"]
                    and x.get("external_id") == suffix and x.get("head_sha") == commit and x.get("status") == "completed"
                    and x.get("details_url") == workflow["html_url"]]
        if {x["name"] for x in verified} != required or len(verified) != len(required):
            raise Invalid("required checks missing or unauthenticated")
        for j in evidence.get("jobs", []):
            checks_for_job = [x for x in verified if x["name"] == "factory-eval/" + j["name"]]
            if len(checks_for_job) != 1 or checks_for_job[0]["conclusion"] != ("success" if j["status"] == "passed" else "failure"):
                raise Invalid("check conclusion does not match trusted evidence")
        graded = score(task, evidence, expected)
        directory = self.destination / record["run_id"]
        write_json(directory / "ci" / f"{suffix}.json", {"workflow": workflow["html_url"], "evidence": evidence}, self.secrets)
        return {"commit": commit, "workflow_run_id": workflow["id"], "grading": graded, "evidence": evidence}

    def drive(self, adapter, result):
        for _ in range(30):
            impl = result.get("implementation") or {}
            if impl.get("clarification"):
                raise Blocked("clarification_required")
            status = impl.get("status")
            if status == "plan_ready":
                result = adapter.call("approve")
            elif status == "amend_ready":
                result = adapter.call("amend")
            elif status == "ready":
                return result
            else:
                raise Blocked("core stopped at " + str(status))
        raise Blocked("too many approval checkpoints")

    def run_one(self, task, trial_index, variant, core_sha, binary):
        c = self.config
        run_id = secrets.token_hex(12)
        trial = Budget(c["trial_limits"], c["pricing"])
        record = {"run_id": run_id, "task_id": task.id, "trial": trial_index + 1, "variant": variant,
                  "core_sha": core_sha, "definition_sha": c["definition_sha"], "task_version": task.data["version"],
                  "task_fingerprint": task.fingerprint, "requirement_ids": [r["id"] for r in task.data["requirements"]],
                  "repository": c["work_repositories"][task.id]["repository"], "config": c,
                  "started": now(), "attempts": [], "ci_repairs_started": 0, "completed": False, "status": "blocked", "unverified": [],
                  "adapter_hash": digest((self.root / "integrations/ai-factory/main.go").read_bytes())}
        directory = self.destination / run_id
        directory.mkdir(parents=True)
        def event(value):
            impl = value.get("checkpoint", {}).get("Implementation") or {}
            if impl.get("pullNumber"):
                record["pull_number"] = impl["pullNumber"]
                # A CI repair draft retains its PR number but omits publication
                # fields until the PR is read. Preserve the last owned identity.
                if impl.get("branch"):
                    record["branch"] = impl["branch"]
                if impl.get("commit"):
                    record["commit"] = impl["commit"]
            elif impl.get("refBranch"):
                record["branch"], record["commit"], record["branch_created"] = impl["refBranch"], impl.get("commit", ""), True
            if impl:
                rounds = record.setdefault("static_repair_rounds", {})
                key = str(impl.get("version", 0))
                rounds[key] = max(rounds.get(key, 0), impl.get("repairRound", 0))
                record["static_repairs"] = sum(rounds.values())
                write_json(directory / "checkpoint.json", value["checkpoint"], self.secrets)
                if value.get("changes"):
                    write_json(directory / "changes" / "checkpoint.json", value["changes"], self.secrets)
            with (directory / "events.jsonl").open("a") as f:
                f.write(redact(json.dumps({"at": now(), **value}, ensure_ascii=False), self.secrets) + "\n")
        adapter = None
        base = c["work_repositories"][task.id]["initial_sha"]
        try:
            self.total.check()
            adapter = Adapter(binary, {"EVAL_MODEL_KEY": os.environ[c["credentials"]["model"]],
                                      "EVAL_WORK_TOKEN": os.environ[c["credentials"]["work"]]}, trial, self.total, event)
            hello = adapter.call("hello")
            if not all(hello.get("capabilities", {}).get(k) for k in ("real_runner", "native_ci", "pre_call_budget", "draft_pr", "followup")):
                raise Blocked("pinned core lacks native CI support; commit the core CI implementation before live evaluation")
            # Verify intended initial failures on GitHub before any repair/model request.
            initial = self.grade(task, record, base, base, trial, "initial")
            observed = {k: v["passed"] for k, v in initial["grading"]["requirements"].items()}
            if observed != task.data["initial"]:
                raise Invalid("initial fixture did not produce its fixed expected failures")
            record["initial_grading"] = initial
            repository = self.work_api.verify_repository(record["repository"])
            initial_pull = 0
            if task.id == "E04":
                # E04 starts with a real failed PR so the production CI-log path
                # sees all initial failures before planning its first correction.
                branch = f"ai-factory/eval-{run_id}-work"
                self.total.check()
                trial.check()
                self.work_api.request("POST", f'/repos/{record["repository"]}/git/refs', {"ref": "refs/heads/" + branch, "sha": base})
                record["branch"], record["commit"], record["branch_created"] = branch, base, True
                self.total.check()
                trial.check()
                pull = self.work_api.request("POST", f'/repos/{record["repository"]}/pulls',
                    {"head": branch, "base": repository["default_branch"], "title": "AI Factory E04 initial failures " + run_id,
                     "body": "Dedicated evaluation run: " + run_id, "draft": True})
                initial_pull = record["pull_number"] = pull["number"]
            request = task.request(0)
            write_json(directory / "requests" / "0.json", {"action": "start", "request": request}, self.secrets)
            result = self.drive(adapter, adapter.call("start", run_id=run_id, task_id=task.id, repository=record["repository"],
                base_commit=base, base_branch=repository["default_branch"], request=request,
                pull_number=initial_pull,
                provider=c["provider"], model=c["model"], allowed_paths=task.data["allowed_paths"], forbidden_paths=task.data["forbidden_paths"],
                max_files=task.data["max_files"], max_bytes=task.data["max_bytes"], seconds=int(trial.limits["seconds"])))
            trace = {}
            def snapshot(result):
                f = next((f for f in result["changes"] if f["path"] == "summary.go"), None)
                if f is None:
                    raise Blocked("E03 intermediate summary.go missing")
                return {**f, "base_exists": False, "published": bool(result["implementation"].get("pullNumber"))}
            for index in range(1, len(task.data["requests"])):
                if task.id == "E03":
                    trace["before_followup"] = snapshot(result)
                write_json(directory / "changes" / "before-followup.json", result, self.secrets)
                request = task.request(index)
                write_json(directory / "requests" / f"{index}.json", {"action": "followup", "request": request}, self.secrets)
                result = self.drive(adapter, adapter.call("followup", request=request))
                if task.id == "E03":
                    trace["after_followup"] = snapshot(result)
            for attempt in range(trial.limits["ci_attempts"]):
                try:
                    task.approve(result["changes"])
                except Invalid as e:
                    raise ImplementationFailed(str(e)) from None
                record["completed"] = result["implementation"].get("status") == "ready"
                event({"type": "publishing", "attempt": attempt})
                write_json(directory / "changes" / f"attempt-{attempt}.json", result, self.secrets)
                patches = []
                for change in result["changes"]:
                    original = task.root / "initial" / change["path"]
                    before = original.read_text() if original.is_file() else ""
                    patches.extend(difflib.unified_diff(before.splitlines(keepends=True), change.get("content", "").splitlines(keepends=True),
                        fromfile="a/"+change["path"], tofile="b/"+change["path"]))
                (directory / "changes" / f"attempt-{attempt}.patch").write_text(redact("".join(patches), self.secrets))
                result = adapter.call("publish")
                impl = result["implementation"]
                record["pull_number"] = impl["pullNumber"]
                # A CI repair draft retains its PR number but omits publication
                # fields until the PR is read. Preserve the last owned identity.
                if impl.get("branch"):
                    record["branch"] = impl["branch"]
                if impl.get("commit"):
                    record["commit"] = impl["commit"]
                graded = self.grade(task, record, impl["commit"], base, trial, trace=trace)
                graded["claimed_complete"] = record["completed"]
                record["attempts"].append(graded)
                write_json(directory / "grading.json", record["attempts"], self.secrets)
                result = adapter.call("check_ci")  # production CI reading path, real GitHub
                record["core_ci_state"] = result["implementation"].get("ci", {}).get("state", "unverified")
                if graded["grading"]["status"] == "passed":
                    break
                if not graded["evidence"].get("logs_complete"):
                    raise Blocked("CI failure evidence incomplete")
                if attempt + 1 < trial.limits["ci_attempts"]:
                    record["ci_repairs_started"] += 1
                    result = self.drive(adapter, adapter.call("fix_ci"))
            record["static_repairs"] = max(record.get("static_repairs", 0), result["implementation"].get("repairRound", 0))
            record["unverified"] = result["implementation"].get("unverified", [])
            record["status"] = classify(record["attempts"])
        except KeyboardInterrupt:
            trial.cancelled = self.total.cancelled = True
            record["status"], record["reason"] = "cancelled", "user cancellation"
        except EvalError as e:
            record["status"], record["reason"] = e.category, str(e)
        except Exception as e:
            record["status"], record["reason"] = "infrastructure_error", "unexpected " + type(e).__name__
        finally:
            if adapter:
                adapter.close()
            # No new writes after cancellation. Cleanup remains explicitly tracked.
            record["cleanup_pending"] = bool(record.get("pull_number") or record.get("branch_created"))
            if c["cleanup"] and record.get("pull_number") and not self.total.cancelled:
                try:
                    self.total.check()
                    self.work_api.cleanup(record["repository"], record["branch"], record["pull_number"], record["commit"], run_id)
                    record["cleanup_pending"] = False
                except EvalError:
                    event({"type": "cleanup_pending"})
            record["usage"], record["finished"] = trial.snapshot(), now()
            write_json(directory / "run.json", record, self.secrets)
            report([record], directory)
        return record

    def run(self):
        self.destination.mkdir(parents=True, exist_ok=False)
        failure = None
        try:
            self.preflight()
            with tempfile.TemporaryDirectory(prefix="factory-core-") as temp:
                binaries = {}
                for core_sha in dict.fromkeys([self.config["baseline_sha"], self.config["candidate_sha"]]):
                    self.total.check()
                    binaries[core_sha] = build_core(self.config["core_path"], core_sha, self.root / "integrations/ai-factory", Path(temp) / core_sha)
                for tid, trial, variant, core_sha in schedule(self.config):
                    task = Task.load(self.root / "tasks" / tid)
                    self.runs.append(self.run_one(task, trial, variant, core_sha, binaries[core_sha]))
                    report(self.runs, self.destination)
        except KeyboardInterrupt:
            self.total.cancelled = True
            failure = Cancelled("user cancellation during preflight/build")
        except EvalError as e:
            failure = e
        except Exception as e:
            failure = Invalid("preflight/build failed: " + type(e).__name__)
        finally:
            if failure:
                for tid, trial, variant, core_sha in schedule(self.config)[len(self.runs):]:
                    task = Task.load(self.root / "tasks" / tid)
                    record = {"run_id": secrets.token_hex(12), "task_id": tid, "trial": trial+1, "variant": variant,
                        "core_sha": core_sha, "definition_sha": self.config["definition_sha"], "started": now(), "finished": now(),
                        "config": self.config, "task_version": task.data["version"],
                        "status": failure.category, "reason": str(failure), "attempts": [], "completed": False,
                        "requirement_ids": [r["id"] for r in task.data["requirements"]], "usage": {},
                        "task_fingerprint": task.fingerprint}
                    self.runs.append(record)
                    write_json(self.destination / record["run_id"] / "run.json", record, self.secrets)
            write_json(self.destination / "usage.json", self.total.snapshot(), self.secrets)
            report(self.runs, self.destination)
        return self.runs
