from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import fnmatch
import json

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
        def matches(pattern):
            # Match Go path.Match in the adapter: wildcards never cross '/'.
            parts, patterns = path.split("/"), pattern.split("/")
            return len(parts) == len(patterns) and all(fnmatch.fnmatchcase(p, g) for p, g in zip(parts, patterns))
        return (any(matches(x) for x in self.data["allowed_paths"])
                and not any(matches(x) for x in self.data["forbidden_paths"]))

    def request(self, index):
        """Expose the same scope enforced by approval, without hidden grading data."""
        scope = {k: self.data[k] for k in ("allowed_paths", "forbidden_paths", "max_files", "max_bytes")}
        return (self.data["requests"][index] + "\n\n"
                "変更範囲（この依頼、追加依頼、CI修正のすべてに適用）:\n"
                "allowed_pathsに一致するパスだけ変更できます。forbidden_pathsが優先します。"
                "forbidden_pathsに一致するファイルは新規追加・変更・削除とも禁止です。"
                "max_filesは変更ファイル数、max_bytesは変更後の全ファイル内容のUTF-8バイト数の上限です。"
                "この範囲内で計画・実装してください。\n" + json.dumps(scope, ensure_ascii=False, sort_keys=True))

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
