from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone


class EvalError(Exception):
    category = "infrastructure_error"


class Invalid(EvalError):
    pass


class Blocked(EvalError):
    category = "blocked"


class ImplementationFailed(EvalError):
    category = "implementation_failed"


class BudgetExceeded(EvalError):
    category = "budget_exceeded"


class Cancelled(EvalError):
    category = "cancelled"


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
        raise Invalid("a full lowercase 40-character commit SHA is required")
    return value


def repo(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*", value) or ".." in value:
        raise Invalid("invalid GitHub repository")
    return value


def safe_path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise Invalid("invalid relative path")
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in ("", ".", "..") for x in value.split("/")):
        raise Invalid("path traversal rejected")
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def redact(text, secrets=()):
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)([^\r\n]+)", r"\1[REDACTED]", text)
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,})", "[REDACTED]", text)
    return re.sub(r"https?://[^\s\"<>]+\?[^\s\"<>]+", "[REDACTED_URL]", text)


def write_json(path, value, secrets=()):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(json.dumps(value, ensure_ascii=False, indent=2) + "\n", secrets))


def command(args, cwd=None, timeout=120, env=None):
    p = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode:
        # Deliberately do not expose subprocess errors or credential-bearing URLs.
        raise Invalid(f"command failed: {Path(args[0]).name} (exit {p.returncode})")
    return p.stdout.strip()


def clean_env():
    return {k: v for k, v in os.environ.items() if k in (
        "PATH", "HOME", "TMPDIR", "SYSTEMROOT", "GOCACHE", "GOMODCACHE", "GOPATH")}
