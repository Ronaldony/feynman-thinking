#!/usr/bin/env python3
"""Validate the feynman-thinking package, eval assets, and root mirror."""

from __future__ import annotations

import ast
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
FILES = ("README.md", "SKILL.md")
DIRS = ("agents", "assets", "evals", "references", "scripts")
REQUIRED = (
    "README.md",
    "SKILL.md",
    "agents/openai.yaml",
    "assets/approximation-ledger.md",
    "assets/feynman-workpad.md",
    "assets/final-report.md",
    "assets/model-card.md",
    "assets/problem-contract.md",
    "assets/unresolved-register.md",
    "assets/verification-record.md",
    "evals/README.md",
    "evals/grading-rubric.json",
    "evals/reasoning-cases.jsonl",
    "evals/trigger-cases.csv",
    "references/evaluation.md",
    "references/evidence-base.md",
    "references/self-critique.md",
    "references/validation-matrix.md",
    "references/workflow.md",
    "scripts/prepare_blind_review.py",
    "scripts/run_eval.py",
    "scripts/sync_mirror.py",
    "scripts/validate_skill.py",
)


def locate() -> tuple[Path, Path, bool]:
    script = Path(__file__).resolve()
    for parent in script.parents:
        package = parent / "skills" / "feynman-thinking"
        if (package / "SKILL.md").is_file():
            return parent, package, True
    package = script.parents[1]
    if (package / "SKILL.md").is_file():
        return package, package, False
    raise RuntimeError("feynman-thinking package not found")


def generated(relative: Path) -> bool:
    parts = relative.parts
    return (
        "__pycache__" in parts
        or relative.suffix.lower() in {".pyc", ".pyo"}
        or relative.name == ".DS_Store"
        or (len(parts) >= 2 and parts[0] == "evals" and parts[1] in {"results", "review"})
    )


def package_files(package: Path) -> dict[Path, Path]:
    return {
        path.relative_to(package): path
        for path in package.rglob("*")
        if path.is_file() and not generated(path.relative_to(package))
    }


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


def scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value[:1] in {"'", '"'}:
        try:
            return str(ast.literal_eval(value))
        except (SyntaxError, ValueError):
            return value.strip("'\"")
    return value


def frontmatter(text: str) -> tuple[dict[str, str], dict[str, str]]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("SKILL.md frontmatter closing delimiter not found")
    top: dict[str, str] = {}
    metadata: dict[str, str] = {}
    section = ""
    for line in text[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if line[0].isspace():
            if section == "metadata":
                metadata[key.strip()] = scalar(value)
        else:
            section = key.strip() if not value.strip() else ""
            top[key.strip()] = scalar(value)
    return top, metadata


def load_json(path: Path, errors: list[str]) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid JSON {path.name}: {exc}")
        return None


def load_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    errors.append(f"invalid JSONL {path.name}:{number}: {exc}")
                    continue
                if not isinstance(row, dict):
                    errors.append(f"{path.name}:{number} must be an object")
                    continue
                rows.append(row)
    except OSError as exc:
        errors.append(f"cannot read {path}: {exc}")
    return rows


def validate_links(package: Path, errors: list[str]) -> int:
    checked = 0
    root = package.resolve()
    for document in package.rglob("*.md"):
        text = document.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            target = target.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            destination = (document.parent / target).resolve()
            try:
                destination.relative_to(root)
            except ValueError:
                errors.append(f"link escapes package: {document.relative_to(package)} -> {target}")
                continue
            checked += 1
            if not destination.exists():
                errors.append(f"broken link: {document.relative_to(package)} -> {target}")
    return checked


def validate_rubric(path: Path, version: str, errors: list[str]) -> tuple[set[str], int]:
    data = load_json(path, errors)
    if not isinstance(data, dict):
        return set(), 0
    if str(data.get("skill_version", "")) != version:
        errors.append("grading-rubric skill_version does not match SKILL.md")
    scale = data.get("score_scale")
    if not isinstance(scale, dict) or not {"0", "1", "2", "N/A"}.issubset(scale):
        errors.append("grading-rubric must define 0, 1, 2, and N/A")
    dimensions = data.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        errors.append("grading-rubric has no dimensions")
        return set(), 0
    ids: list[str] = []
    for number, item in enumerate(dimensions, 1):
        if not isinstance(item, dict):
            errors.append(f"rubric dimension {number} must be an object")
            continue
        dimension = str(item.get("id", "")).strip()
        if not dimension:
            errors.append(f"rubric dimension {number} has no id")
            continue
        ids.append(dimension)
        for score in ("0", "1", "2"):
            if not str(item.get(score, "")).strip():
                errors.append(f"rubric dimension {dimension} lacks score {score}")
    if len(ids) != len(set(ids)):
        errors.append("grading-rubric has duplicate dimension ids")
    gates = data.get("hard_gates")
    gate_count = len(gates) if isinstance(gates, list) else 0
    if gate_count < 5:
        errors.append("grading-rubric must define at least five hard gates")
    if not isinstance(data.get("success_criteria"), dict):
        errors.append("grading-rubric must define success_criteria")
    return set(ids), gate_count


def validate_cases(path: Path, dimensions: set[str], errors: list[str], warnings: list[str]) -> int:
    rows = load_jsonl(path, errors)
    required = {
        "id", "domain", "difficulty", "level", "prompt",
        "expected_behaviors", "forbidden_shortcuts", "applicable_dimensions",
    }
    ids: set[str] = set()
    domains: set[str] = set()
    for number, row in enumerate(rows, 1):
        missing = required - row.keys()
        if missing:
            errors.append(f"reasoning case {number} missing fields: {sorted(missing)}")
            continue
        case_id = str(row.get("id", "")).strip()
        if not case_id or case_id in ids:
            errors.append(f"missing or duplicate reasoning case id: {case_id!r}")
        ids.add(case_id)
        domains.add(str(row.get("domain", "")))
        if row.get("level") not in {"L0", "L1", "L2", "L3"}:
            errors.append(f"reasoning case {case_id} has invalid level")
        for key in ("expected_behaviors", "forbidden_shortcuts", "applicable_dimensions"):
            if not isinstance(row.get(key), list) or not row.get(key):
                errors.append(f"reasoning case {case_id} requires non-empty {key}")
        unknown = {str(item) for item in row.get("applicable_dimensions", [])} - dimensions
        if unknown:
            errors.append(f"reasoning case {case_id} uses unknown dimensions: {sorted(unknown)}")
    if len(rows) < 10:
        warnings.append(f"reasoning-case set is small: {len(rows)}")
    if len(domains) < 6:
        warnings.append(f"reasoning-case domain coverage is narrow: {len(domains)}")
    return len(rows)


def validate_triggers(path: Path, errors: list[str], warnings: list[str]) -> int:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fields = set(reader.fieldnames or [])
    except OSError as exc:
        errors.append(f"cannot read trigger-cases.csv: {exc}")
        return 0
    required = {"prompt", "should_trigger", "reason"}
    if not required.issubset(fields):
        errors.append(f"trigger-cases.csv missing columns: {sorted(required - fields)}")
    prompts: set[str] = set()
    yes = no = 0
    for number, row in enumerate(rows, 2):
        prompt = (row.get("prompt") or "").strip()
        expected = (row.get("should_trigger") or "").strip().lower()
        if not prompt or not (row.get("reason") or "").strip():
            errors.append(f"trigger-cases.csv:{number} requires prompt and reason")
        if prompt in prompts:
            errors.append(f"duplicate trigger prompt at row {number}")
        prompts.add(prompt)
        if expected == "yes":
            yes += 1
        elif expected == "no":
            no += 1
        else:
            errors.append(f"trigger-cases.csv:{number} should_trigger must be yes or no")
    if yes < 5 or no < 5:
        warnings.append(f"trigger balance is weak: yes={yes}, no={no}")
    return len(rows)


def validate_scripts(path: Path, errors: list[str]) -> int:
    count = 0
    for script in sorted(path.glob("*.py")):
        try:
            compile(script.read_text(encoding="utf-8"), str(script), "exec")
        except (OSError, SyntaxError) as exc:
            errors.append(f"Python syntax error in {script.name}: {exc}")
        count += 1
    return count


def validate_mirror(repository: Path, package: Path, errors: list[str]) -> tuple[int, int]:
    canonical = package_files(package)
    mirror = mirror_files(repository)
    for relative in sorted(canonical.keys() - mirror.keys()):
        errors.append(f"root mirror missing: {relative.as_posix()}")
    for relative in sorted(mirror.keys() - canonical.keys()):
        errors.append(f"root mirror has extra file: {relative.as_posix()}")
    for relative in sorted(canonical.keys() & mirror.keys()):
        if canonical[relative].read_bytes() != mirror[relative].read_bytes():
            errors.append(f"root mirror drift: {relative.as_posix()}")
    return len(canonical), len(mirror)


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    repository, package, source_repository = locate()
    for relative in REQUIRED:
        if not (package / relative).exists():
            errors.append(f"required package component missing: {relative}")
    try:
        skill_text = (package / "SKILL.md").read_text(encoding="utf-8")
        top, metadata = frontmatter(skill_text)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    name = top.get("name", "")
    description = top.get("description", "")
    version = metadata.get("version", "")
    if not name or not NAME_RE.fullmatch(name) or name != package.name or len(name) > 64:
        errors.append(f"invalid frontmatter name: {name!r}")
    if not description or len(description) > 1024:
        errors.append(f"invalid description length: {len(description)}")
    if not version:
        errors.append("metadata.version is required")
    lines = len(skill_text.splitlines())
    if lines > 500:
        warnings.append(f"SKILL.md has {lines} lines; recommended maximum is 500")

    links = validate_links(package, errors)
    dimensions, gates = validate_rubric(package / "evals" / "grading-rubric.json", version, errors)
    cases = validate_cases(package / "evals" / "reasoning-cases.jsonl", dimensions, errors, warnings)
    triggers = validate_triggers(package / "evals" / "trigger-cases.csv", errors, warnings)
    scripts = validate_scripts(package / "scripts", errors)
    agent_text = (package / "agents" / "openai.yaml").read_text(encoding="utf-8")
    for marker in ("default_prompt:", "allow_implicit_invocation: true"):
        if marker not in agent_text:
            errors.append(f"agents/openai.yaml missing marker: {marker}")

    canonical_count = len(package_files(package))
    mirror_count = 0
    if source_repository:
        canonical_count, mirror_count = validate_mirror(repository, package, errors)
    for path in package.rglob("*"):
        if path.is_file() and not generated(path.relative_to(package)):
            try:
                path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                errors.append(f"non-UTF-8 text file: {path.relative_to(package)}")

    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"Validation failed: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"Validation passed: {name} v{version}")
    print(f"Description characters: {len(description)}")
    print(f"SKILL.md lines: {lines}")
    print(f"Canonical files: {canonical_count}")
    if source_repository:
        print(f"Mirror files: {mirror_count}")
    print(f"Local Markdown links checked: {links}")
    print(f"Trigger cases: {triggers}")
    print(f"Reasoning cases: {cases}")
    print(f"Rubric dimensions: {len(dimensions)}; hard gates: {gates}")
    print(f"Python scripts compiled: {scripts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
