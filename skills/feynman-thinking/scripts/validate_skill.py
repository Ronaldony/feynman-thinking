#!/usr/bin/env python3
"""Static validation for the feynman-thinking Agent Skill.

This checks package structure, frontmatter, local links, eval data, JSON schemas,
and Python syntax. It does not prove that a model follows the skill or that the
skill improves outcomes; run the behavioral eval harness for that evidence.
"""

from __future__ import annotations

import ast
import csv
import json
import py_compile
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
REQUIRED_CASE_FIELDS = {
    "id",
    "domain",
    "risk_level",
    "prompt",
    "expected_findings",
    "required_behaviors",
    "optional_behaviors",
    "hard_failures",
    "notes",
}
REQUIRED_DIMENSIONS = {
    "problem_reframe",
    "mechanism",
    "independent_representation",
    "direct_check",
    "approximation_honesty",
    "competing_model",
    "discriminating_test",
    "adverse_evidence_revision",
    "plain_precise_link",
    "uncertainty_calibration",
    "actionability",
    "efficiency",
}


def scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value[0:1] in {'"', "'"}:
        try:
            return str(ast.literal_eval(value))
        except (SyntaxError, ValueError):
            return value.strip('"\'')
    return value


def parse_top_level_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with '---' YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("YAML frontmatter closing delimiter not found")
    result: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = scalar(value)
    return result


def load_reasoning_cases(path: Path, errors: list[str]) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    ids: set[str] = set()
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_no}: invalid JSON: {exc}")
            continue
        missing = REQUIRED_CASE_FIELDS - set(case)
        if missing:
            errors.append(f"{path.name}:{line_no}: missing fields {sorted(missing)}")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{path.name}:{line_no}: id must be a non-empty string")
        elif case_id in ids:
            errors.append(f"{path.name}:{line_no}: duplicate id {case_id}")
        else:
            ids.add(case_id)
        for field in ("expected_findings", "required_behaviors", "optional_behaviors", "hard_failures"):
            value = case.get(field)
            if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                errors.append(f"{path.name}:{line_no}: {field} must be a list of non-empty strings")
        required = set(case.get("required_behaviors", []))
        optional = set(case.get("optional_behaviors", []))
        unknown = (required | optional) - REQUIRED_DIMENSIONS
        if unknown:
            errors.append(f"{path.name}:{line_no}: unknown behavior ids {sorted(unknown)}")
        overlap = required & optional
        if overlap:
            errors.append(f"{path.name}:{line_no}: behaviors both required and optional {sorted(overlap)}")
        cases.append(case)
    if not cases:
        errors.append(f"{path.name}: no cases found")
    return cases


def validate_trigger_cases(path: Path, errors: list[str]) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"id", "prompt", "should_trigger", "mode", "reason"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            errors.append(f"{path.name}: required columns are {sorted(required)}")
            return
        ids: set[str] = set()
        seen = {"yes": 0, "no": 0}
        for row_no, row in enumerate(reader, 2):
            case_id = (row.get("id") or "").strip()
            if not case_id:
                errors.append(f"{path.name}:{row_no}: empty id")
            elif case_id in ids:
                errors.append(f"{path.name}:{row_no}: duplicate id {case_id}")
            else:
                ids.add(case_id)
            value = (row.get("should_trigger") or "").strip().lower()
            if value not in seen:
                errors.append(f"{path.name}:{row_no}: should_trigger must be yes or no")
            else:
                seen[value] += 1
        if not all(seen.values()):
            errors.append(f"{path.name}: include both positive and negative controls")


def validate_grading_schema(path: Path, errors: list[str]) -> None:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{path.name}: invalid JSON: {exc}")
        return
    try:
        enum = set(
            schema["properties"]["dimensions"]["items"]["properties"]["id"]["enum"]
        )
    except (KeyError, TypeError):
        errors.append(f"{path.name}: dimension enum not found")
        return
    if enum != REQUIRED_DIMENSIONS:
        errors.append(
            f"{path.name}: dimension enum mismatch; missing={sorted(REQUIRED_DIMENSIONS - enum)}, "
            f"extra={sorted(enum - REQUIRED_DIMENSIONS)}"
        )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    skill_md = root / "SKILL.md"
    errors: list[str] = []
    warnings: list[str] = []

    required_paths = [
        skill_md,
        root / "agents/openai.yaml",
        root / "references/workflow.md",
        root / "references/validation-matrix.md",
        root / "references/evidence-base.md",
        root / "references/self-critique.md",
        root / "references/evaluation.md",
        root / "evals/trigger-cases.csv",
        root / "evals/reasoning-cases.jsonl",
        root / "evals/grading-schema.json",
        root / "scripts/run_evals.py",
        root / "scripts/grade_evals.py",
    ]
    for path in required_paths:
        if not path.is_file():
            errors.append(f"required file missing: {path.relative_to(root)}")

    if errors and not skill_md.is_file():
        for item in errors:
            print(f"ERROR: {item}", file=sys.stderr)
        return 1

    try:
        text = skill_md.read_text(encoding="utf-8")
        fm = parse_top_level_frontmatter(text)
    except (UnicodeDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    name = fm.get("name", "")
    description = fm.get("description", "")
    if not name:
        errors.append("frontmatter field 'name' is required")
    elif len(name) > 64:
        errors.append("name must be at most 64 characters")
    elif not NAME_RE.fullmatch(name):
        errors.append("name must contain lowercase letters, digits, and single hyphens only")
    elif name != root.name:
        errors.append(f"name '{name}' must match parent directory '{root.name}'")

    if not description:
        errors.append("frontmatter field 'description' is required")
    elif len(description) > 1024:
        errors.append(f"description is {len(description)} characters; maximum is 1024")

    line_count = len(text.splitlines())
    if line_count > 500:
        warnings.append(f"SKILL.md has {line_count} lines; recommended maximum is 500")

    for target in LINK_RE.findall(text):
        target = target.split("#", 1)[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        path = (root / target).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            errors.append(f"link escapes skill root: {target}")
            continue
        if not path.exists():
            errors.append(f"broken local link: {target}")

    if (root / "evals/reasoning-cases.jsonl").is_file():
        load_reasoning_cases(root / "evals/reasoning-cases.jsonl", errors)
    if (root / "evals/trigger-cases.csv").is_file():
        validate_trigger_cases(root / "evals/trigger-cases.csv", errors)
    if (root / "evals/grading-schema.json").is_file():
        validate_grading_schema(root / "evals/grading-schema.json", errors)

    for script in (root / "scripts").glob("*.py"):
        try:
            py_compile.compile(str(script), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append(f"Python syntax error in {script.name}: {exc.msg}")

    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() not in {
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pyc"
        }:
            try:
                path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                warnings.append(f"non-UTF-8 file: {path.relative_to(root)}")

    if root.parent.name == "skills":
        repository_root = root.parent.parent
        duplicate = repository_root / "SKILL.md"
        if duplicate.exists():
            errors.append("duplicate root SKILL.md found; keep skills/feynman-thinking as the single source of truth")

    for item in warnings:
        print(f"WARNING: {item}")
    if errors:
        for item in errors:
            print(f"ERROR: {item}", file=sys.stderr)
        print(f"Validation failed: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1

    file_count = sum(1 for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    print(f"Validation passed: {name}")
    print(f"Description characters: {len(description)}")
    print(f"SKILL.md lines: {line_count}")
    print(f"Files: {file_count}")
    print("Behavioral performance is not proven by this static check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
