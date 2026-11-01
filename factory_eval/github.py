from __future__ import annotations

import io
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import zipfile

from .util import Invalid, repo, sha


class StripCredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if urllib.parse.urlparse(newurl).scheme != "https":
            raise Invalid("non-HTTPS GitHub redirect rejected")
        if urllib.parse.urlparse(req.full_url).netloc != urllib.parse.urlparse(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


class GitHub:
    def __init__(self, token, *, opener=None):
        self.token = token
        self.opener = opener or urllib.request.build_opener(StripCredentialRedirect()).open

    def request(self, method, path, data=None, *, raw=False):
        # API error bodies can include secrets: never propagate them.
        req = urllib.request.Request("https://api.github.com" + path,
            data=json.dumps(data).encode() if data is not None else None, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=30) as response:
                body = response.read(20_000_001)
                if len(body) > 20_000_000:
                    raise Invalid("GitHub response limit")
                return body if raw else json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            raise Invalid(f"GitHub HTTP {e.code}") from None
        except (OSError, ValueError):
            raise Invalid("GitHub API unavailable or invalid response") from None

    def pages(self, path, key=None):
        rows = []
        for n in range(1, 101):
            page = self.request("GET", path + ("&" if "?" in path else "?") + f"per_page=100&page={n}")
            items = page[key] if key else page
            rows.extend(items)
            if len(items) < 100:
                return rows
        raise Invalid("GitHub pagination incomplete")

    def verify_repository(self, repository):
        repo(repository)
        result = self.request("GET", f"/repos/{repository}")
        if result["full_name"].lower() != repository.lower() or result.get("archived") or result.get("fork"):
            raise Invalid("work repository must be an active dedicated non-fork repository")
        return result

    def resolve(self, repository, ref):
        out = self.request("GET", f"/repos/{repo(repository)}/commits/{urllib.parse.quote(ref, safe='')}")
        return sha(out["sha"])

    def changed_files(self, repository, base, head):
        out = self.request("GET", f"/repos/{repo(repository)}/compare/{sha(base)}...{sha(head)}?per_page=100")
        files = out.get("files", [])
        if len(files) >= 300 or out.get("status") not in ("ahead", "identical") or out.get("merge_base_commit", {}).get("sha") != base:
            raise Invalid("incomplete diff or changed ancestry")
        return files

    def dispatch(self, evaluation_repo, workflow_ref, definition_sha, inputs):
        if self.resolve(evaluation_repo, workflow_ref) != definition_sha:
            raise Invalid("workflow ref does not point at pinned evaluation SHA")
        self.request("POST", f"/repos/{repo(evaluation_repo)}/actions/workflows/grade.yml/dispatches",
                     {"ref": workflow_ref, "inputs": inputs})

    def find_run(self, repository, run_id, definition_sha):
        runs = self.pages(f"/repos/{repo(repository)}/actions/workflows/grade.yml/runs?event=workflow_dispatch", "workflow_runs")
        matches = [r for r in runs if r.get("display_title") == run_id and r.get("head_sha") == definition_sha]
        if len(matches) > 1:
            raise Invalid("ambiguous grading workflow run")
        return matches[0] if matches else None

    def evidence(self, repository, run, artifact_name):
        artifacts = self.pages(f'/repos/{repo(repository)}/actions/runs/{run["id"]}/artifacts', "artifacts")
        matches = [a for a in artifacts if a["name"] == artifact_name and not a["expired"]]
        if len(matches) != 1:
            raise Invalid("missing or ambiguous trusted grading artifact")
        raw = self.request("GET", f'/repos/{repository}/actions/artifacts/{matches[0]["id"]}/zip', raw=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            files = [x for x in z.infolist() if x.filename == "grading.json"]
            if len(files) != 1 or files[0].file_size > 2_000_000:
                raise Invalid("invalid grading artifact")
            return json.loads(z.read(files[0]))

    def cleanup(self, repository, branch, number, expected_head, run_id):
        # Verify the PR and current branch before either mutation. Never delete a moved branch.
        p = self.request("GET", f"/repos/{repo(repository)}/pulls/{int(number)}")
        if (not branch.startswith(f"ai-factory/eval-{run_id}-") or p["head"]["ref"] != branch
                or p["head"]["sha"] != expected_head or p["head"]["repo"]["full_name"] != repository
                or not p.get("draft") or p.get("merged")):
            raise Invalid("cleanup ownership mismatch")
        ref = self.request("GET", f"/repos/{repository}/git/ref/heads/{urllib.parse.quote(branch, safe='')}")
        if ref["object"]["sha"] != expected_head:
            raise Invalid("cleanup branch moved")
        self.request("PATCH", f"/repos/{repository}/pulls/{number}", {"state": "closed"})
        self.request("DELETE", f"/repos/{repository}/git/refs/heads/{urllib.parse.quote(branch, safe='')}")
