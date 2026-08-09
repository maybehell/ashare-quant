from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


MAX_FILE_BYTES = 5 * 1024 * 1024

DENIED_PARTS = {
    ".venv",
    ".tmp",
    ".pip-cache",
    ".pytest_cache",
    "__pycache__",
    "data",
    "logger",
    "output",
    "test_outputs",
}

DENIED_SUFFIXES = {
    ".db",
    ".dpapi",
    ".feather",
    ".joblib",
    ".key",
    ".p12",
    ".parquet",
    ".pem",
    ".pfx",
    ".pickle",
    ".pkl",
    ".sqlite",
    ".sqlite3",
}

DENIED_NAMES = {
    ".env",
    "credentials.json",
    "secrets.json",
}

SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "OpenAI key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "QQ mailbox": re.compile(r"\b[1-9][0-9]{4,}@qq\.com\b", re.IGNORECASE),
}


def _git(repo: Path, *args: str) -> list[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def staged_paths(repo: Path) -> list[str]:
    return _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR")


def tracked_paths(repo: Path) -> list[str]:
    return _git(repo, "ls-files")


def path_problem(relative: str) -> str | None:
    path = PurePosixPath(relative.replace("\\", "/"))
    lowered_parts = [part.lower() for part in path.parts]
    name = path.name.lower()

    if any(part.startswith("outputs") for part in lowered_parts):
        return "generated outputs directory"
    if any(part.startswith(".codex") for part in lowered_parts):
        return "local Codex temporary artifact"
    if any(part in DENIED_PARTS for part in lowered_parts):
        return "denied local/generated path"
    if name in DENIED_NAMES or name.startswith(".env."):
        return "credential/environment file"
    if path.suffix.lower() in DENIED_SUFFIXES:
        return "sensitive or generated binary/state file"
    return None


def content_problems(path: Path) -> list[str]:
    if path.stat().st_size > MAX_FILE_BYTES:
        return [f"file exceeds {MAX_FILE_BYTES} bytes"]

    raw = path.read_bytes()
    if b"\x00" in raw:
        return ["binary file is not allowed by the source sync policy"]

    text = raw.decode("utf-8", errors="replace")
    return [label for label, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


def audit(repo: Path, paths: list[str]) -> list[str]:
    failures: list[str] = []
    for relative in sorted(set(paths)):
        problem = path_problem(relative)
        if problem:
            failures.append(f"{relative}: {problem}")
            continue

        absolute = repo / relative
        if not absolute.is_file():
            failures.append(f"{relative}: path is missing or is not a regular file")
            continue
        for detail in content_problems(absolute):
            failures.append(f"{relative}: {detail}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Git source sync preflight")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--mode", choices=("staged", "tracked"), default="staged")
    args = parser.parse_args()

    repo = args.repo.resolve()
    try:
        paths = staged_paths(repo) if args.mode == "staged" else tracked_paths(repo)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: unable to enumerate Git files: {exc}", file=sys.stderr)
        return 2

    if not paths:
        print(f"Git sync preflight: no {args.mode} files to audit")
        return 0

    failures = audit(repo, paths)
    if failures:
        print("Git sync preflight FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"Git sync preflight: ok ({len(paths)} {args.mode} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
