from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

LOCK_PATH = Path(__file__).resolve().parent.parent / "constraints.lock"
ROOT_DISTRIBUTION = "media-uploader"


def read_lock() -> dict[str, str]:
    locked: dict[str, str] = {}
    for raw_line in LOCK_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", line)
        if match is None:
            raise SystemExit(f"Unsupported lock line: {raw_line!r}")
        locked[canonicalize_name(match.group(1))] = match.group(2)
    return locked


def runtime_graph() -> dict[str, str]:
    resolved: dict[str, str] = {}
    pending = [canonicalize_name(ROOT_DISTRIBUTION)]
    seen: set[str] = set()

    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        distribution = metadata.distribution(name)
        for requirement_text in distribution.requires or []:
            requirement = Requirement(requirement_text)
            if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                continue
            dependency = canonicalize_name(requirement.name)
            if dependency == canonicalize_name(ROOT_DISTRIBUTION):
                continue
            dependency_dist = metadata.distribution(dependency)
            resolved[dependency] = dependency_dist.version
            pending.append(dependency)
    return resolved


def main() -> None:
    locked = read_lock()
    resolved = runtime_graph()

    missing = sorted(set(resolved) - set(locked))
    stale = sorted(set(locked) - set(resolved))
    mismatched = sorted(
        name for name, version in resolved.items() if locked.get(name) is not None and locked[name] != version
    )

    problems: list[str] = []
    if missing:
        problems.append(f"missing runtime lock entries: {', '.join(missing)}")
    if stale:
        problems.append(f"stale runtime lock entries: {', '.join(stale)}")
    if mismatched:
        problems.append(
            "version mismatches: "
            + ", ".join(f"{name} installed={resolved[name]} locked={locked[name]}" for name in mismatched)
        )
    if problems:
        raise SystemExit("Runtime dependency lock is incomplete or stale:\n- " + "\n- ".join(problems))

    print(f"Runtime dependency lock verified: {len(resolved)} packages")


if __name__ == "__main__":
    main()
