from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import fnmatch

from .util import Invalid, read_json, safe_path, digest


@dataclass(frozen=True)
class Task:
    root: Path
    data: dict

    @classmethod
    def load(cls, root):
        root = Path(root).resolve()
        d = read_json(root / "task.json")
        required = {"id", "version", "purpose", "requests", "allowed_paths", "forbidden_paths",
                    "max_files", "max_bytes", "requirements", "required_jobs", "initial", "reference", "mutations"}
        if set(d) != required or d["version"] < 1 or not d["requests"]:
            raise Invalid("invalid task definition")
        ids = [x["id"] for x in d["requirements"]]
        if len(ids) != len(set(ids)) or not ids:
            raise Invalid("duplicate or empty requirement IDs")
        if set(d["required_jobs"]) != {"build", "requirements", "regression"}:
            raise Invalid("build, requirements and regression jobs are mandatory")
        for r in d["requirements"]:
            if not r.get("tests") or r.get("job") not in d["required_jobs"]:
                raise Invalid("requirement without executable evidence")
        for expectation in (d["initial"], d["reference"]):
            if set(expectation) != set(ids):
                raise Invalid("expectations must cover every fixed requirement")
        if not all(d["reference"].values()):
            raise Invalid("reference must pass every requirement")
        return cls(root, d)

    @property
    def id(self):
        return self.data["id"]

    @property
    def fingerprint(self):
        return digest(b"".join(str(p.relative_to(self.root)).encode() + b"\0" + p.read_bytes()
                              for p in sorted(self.root.rglob("*")) if p.is_file()))

    def permits(self, path):
        safe_path(path)
        return (any(fnmatch.fnmatchcase(path, x) for x in self.data["allowed_paths"])
                and not any(fnmatch.fnmatchcase(path, x) for x in self.data["forbidden_paths"]))

    def approve(self, files):
        if not files or len(files) > self.data["max_files"]:
            raise Invalid("change count outside task scope")
        seen, size = set(), 0
        for f in files:
            if f["path"] in seen or not self.permits(f["path"]) or f["action"] not in ("create", "update", "delete"):
                raise Invalid("change outside task scope")
            seen.add(f["path"])
            size += len(f.get("content", "").encode())
        if size > self.data["max_bytes"]:
            raise Invalid("change size outside task scope")
        return True
