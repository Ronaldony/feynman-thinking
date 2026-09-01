#!/usr/bin/env python3
"""Synchronize the repository-root mirror from skills/feynman-thinking."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path

FILES = ("README.md", "SKILL.md")
DIRS = ("agents", "assets", "evals", "references", "scripts")


def locate() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    for parent in script.parents:
        package = parent / "skills" / "feynman-thinking"
        if (package / "SKILL.md").is_file():
            return parent, package
    raise RuntimeError("source repository containing skills/feynman-thinking not found")


def generated(relative: Path) -> bool:
    parts = relative.parts
    return (
        "__pycache__" in parts
        or relative.suffix.lower() in {".pyc", ".pyo"}
        or relative.name == ".DS_Store"
        or (len(parts) >= 2 and parts[0] == "evals" and parts[1] in {"results", "review"})
    )


def canonical_files(package: Path) -> dict[Path, Path]:
    result: dict[Path, Path] = {}
    for name in FILES:
        source = package / name
        if not source.is_file():
            raise FileNotFoundError(f"canonical file missing: {source}")
        result[Path(name)] = source
    for directory in DIRS:
        root = package / directory
        if not root.is_dir():
            raise FileNotFoundError(f"canonical directory missing: {root}")
        for source in root.rglob("*"):
            if source.is_file():
                relative = source.relative_to(package)
                if not generated(relative):
                    result[relative] = source
    return result


def mirror_files(repository: Path) -> dict[Path, Path]:
    result: dict[Path, Path] = {}
    for name in FILES:
        path = repository / name
        if path.is_file():
            result[Path(name)] = path
    for directory in DIRS:
        root = repository / directory
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                relative = path.relative_to(repository)
                if not generated(relative):
                    result[relative] = path
    return result


def drift(repository: Path, package: Path) -> list[str]:
    desired = canonical_files(package)
    current = mirror_files(repository)
    messages: list[str] = []
    for relative in sorted(desired.keys() - current.keys()):
        messages.append(f"missing mirror file: {relative.as_posix()}")
    for relative in sorted(current.keys() - desired.keys()):
        messages.append(f"extra mirror file: {relative.as_posix()}")
    for relative in sorted(desired.keys() & current.keys()):
        if desired[relative].read_bytes() != current[relative].read_bytes():
            messages.append(f"content drift: {relative.as_posix()}")
    return messages


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.read_bytes() == source.read_bytes():
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source.read_bytes())
        try:
            shutil.copymode(source, temporary)
        except OSError:
            pass
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def synchronize(repository: Path, package: Path) -> tuple[int, int]:
    desired = canonical_files(package)
    current = mirror_files(repository)
    copied = removed = 0
    for relative in sorted(current.keys() - desired.keys()):
        current[relative].unlink()
        removed += 1
    for relative, source in sorted(desired.items(), key=lambda item: item[0].as_posix()):
        destination = repository / relative
        if not destination.is_file() or destination.read_bytes() != source.read_bytes():
            atomic_copy(source, destination)
            copied += 1
    for directory in DIRS:
        root = repository / directory
        if root.is_dir():
            children = (path for path in root.rglob("*") if path.is_dir())
            for path in sorted(children, key=lambda item: len(item.parts), reverse=True):
                if not generated(path.relative_to(repository)):
                    try:
                        path.rmdir()
                    except OSError:
                        pass
    return copied, removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    args = parser.parse_args()
    repository, package = locate()
    if args.check:
        differences = drift(repository, package)
        if differences:
            for item in differences:
                print(f"ERROR: {item}")
            print(f"Mirror check failed: {len(differences)} difference(s)")
            return 1
        print(f"Mirror check passed: {len(canonical_files(package))} file(s)")
        return 0
    copied, removed = synchronize(repository, package)
    differences = drift(repository, package)
    if differences:
        for item in differences:
            print(f"ERROR: {item}")
        return 1
    print(
        f"Mirror synchronized: {len(canonical_files(package))} file(s), "
        f"{copied} copied, {removed} removed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
