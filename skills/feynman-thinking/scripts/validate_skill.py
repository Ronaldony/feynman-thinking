#!/usr/bin/env python3
"""Static validation for the feynman-thinking Agent Skill.

This checks package structure, frontmatter, all local Markdown links, eval data,
JSON schema consistency, Python syntax, and dry-run job assembly. It does not
prove that a model follows the skill or that the skill improves outcomes.
"""

from __future__ import annotations

import ast
import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

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
REQUIRED_PATHS = (
    "SKILL.md",
    "agents/openai.yaml",
    "assets/approximation-ledger.md",
    "assets/final-report.md",
    "assets/model-card.md",
    "assets/problem-contract.md",
    "assets/unresolved-register.md",
    "assets/verification-record.md",
    "evals/README.md",
    "evals/grading-schema.json",
    "evals/reasoning-cases.jsonl",
    "evals/trigger-cases.csv",
    "references/workflow.md",
    "references/validation-matrix.md",
    "references/evidence-base.md",
    "references/self-critique.md",
    "references/evaluation.md",
    "scripts/run_evals.py",
    "scripts/grade_evals.py",
    "scripts/prepare_blind_review.py",
    "scripts/validate_skill.py",
)


def scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value[:1] in {'"', "'"}:
        try:
            return str(ast.literal_eval(value))
        except (SyntaxError, ValueError):
            return value.strip('"\'')
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, str], dict[str, str]]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with '---' YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("YAML frontmatter closing delimiter not found")
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
            continue
        if value.strip():
            top[key.strip()] = scalar(value)
            section = ""
        else:
            section = key.strip()
    return top, metadata


def load_reasoning_cases(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    ids: set[str] = set()
    domains: set[str] = set()
    for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_no}: invalid JSON: {exc}")
            continue
        if not isinstance(case, dict):
            errors.append(f"{path.name}:{line_no}: case must be an object")
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
        domain = case.get("domain")
        if isinstance(domain, str) and domain:
            domains.add(domain)
        if case.get("risk_level") not in {"L0", "L1", "L2", "L3"}:
            errors.append(f"{path.name}:{line_no}: invalid risk_level")
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
    if len(cases) < 10:
        errors.append(f"{path.name}: include at least 10 held-out cases; found {len(cases)}")
    if len(domains) < 6:
        errors.append(f"{path.name}: include at least 6 domains; found {len(domains)}")
    return cases


def validate_trigger_cases(path: Path, errors: list[str]) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"id", "prompt", "should_trigger", "mode", "reason"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            errors.append(f"{path.name}: required columns are {sorted(required)}")
            return 0
        ids: set[str] = set()
        seen = {"yes": 0, "no": 0}
        modes: set[str] = set()
        rows = 0
        for row_no, row in enumerate(reader, 2):
            rows += 1
            case_id = (row.get("id") or "").strip()
            prompt = (row.get("prompt") or "").strip()
            reason = (row.get("reason") or "").strip()
            mode = (row.get("mode") or "").strip()
            if not case_id or not prompt or not reason or not mode:
                errors.append(f"{path.name}:{row_no}: id, prompt, mode, and reason are required")
            elif case_id in ids:
                errors.append(f"{path.name}:{row_no}: duplicate id {case_id}")
            else:
                ids.add(case_id)
            modes.add(mode)
            value = (row.get("should_trigger") or "").strip().lower()
            if value not in seen:
                errors.append(f"{path.name}:{row_no}: should_trigger must be yes or no")
            else:
                seen[value] += 1
        if min(seen.values()) < 5:
            errors.append(f"{path.name}: include at least five positive and five negative controls")
        if not {"explicit", "implicit"}.issubset(modes):
            errors.append(f"{path.name}: include explicit and implicit activation modes")
        return rows


def validate_grading_schema(path: Path, errors: list[str]) -> None:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{path.name}: invalid JSON: {exc}")
        return
    try:
        item_properties = schema["properties"]["dimensions"]["items"]["properties"]
        ids = set(item_properties["id"]["enum"])
        scores = set(item_properties["score"]["enum"])
    except (KeyError, TypeError):
        errors.append(f"{path.name}: dimension id/score enums not found")
        return
    if ids != REQUIRED_DIMENSIONS:
        errors.append(
            f"{path.name}: dimension enum mismatch; missing={sorted(REQUIRED_DIMENSIONS - ids)}, "
            f"extra={sorted(ids - REQUIRED_DIMENSIONS)}"
        )
    if scores != {"0", "1", "2", "NA"}:
        errors.append(f"{path.name}: score enum must be 0, 1, 2, and NA")
    top_required = set(schema.get("required", []))
    expected = {
        "overall_pass",
        "hard_failures",
        "expected_findings",
        "dimensions",
        "general_normalized_score",
        "feynman_specific_score",
        "confidence",
        "summary",
    }
    if top_required != expected:
        errors.append(f"{path.name}: top-level required fields mismatch")


def validate_markdown_links(skill_root: Path, repository_root: Path, errors: list[str]) -> int:
    documents = list(skill_root.rglob("*.md"))
    root_readme = repository_root / "README.md"
    if root_readme.is_file():
        documents.append(root_readme)
    checked = 0
    allowed_root = repository_root.resolve()
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            clean = target.split("#", 1)[0].strip()
            if not clean or clean.startswith(("http://", "https://", "mailto:")):
                continue
            destination = (document.parent / clean).resolve()
            try:
                destination.relative_to(allowed_root)
            except ValueError:
                errors.append(f"link escapes repository: {document.relative_to(repository_root)} -> {clean}")
                continue
            checked += 1
            if not destination.exists():
                errors.append(f"broken link: {document.relative_to(repository_root)} -> {clean}")
    return checked


def validate_python_scripts(path: Path, errors: list[str]) -> int:
    count = 0
    for script in sorted(path.glob("*.py")):
        try:
            source = script.read_text(encoding="utf-8")
            compile(source, str(script), "exec")
        except (OSError, SyntaxError) as exc:
            errors.append(f"Python syntax error in {script.name}: {exc}")
        count += 1
    return count


def validate_dry_run(root: Path, case_id: str, errors: list[str]) -> int:
    command = [
        sys.executable,
        str(root / "scripts" / "run_evals.py"),
        "--case",
        case_id,
        "--conditions",
        "baseline",
        "generic",
        "feynman",
        "feynman-implicit",
        "--dry-run",
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        errors.append(f"run_evals.py dry-run failed: {completed.stderr.strip()}")
        return 0
    rows = []
    for number, raw in enumerate(completed.stdout.splitlines(), 1):
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            errors.append(f"run_evals.py dry-run line {number} is not JSON: {exc}")
    conditions = {str(row.get("condition", "")) for row in rows if isinstance(row, dict)}
    if conditions != {"baseline", "generic", "feynman", "feynman-implicit"}:
        errors.append(f"run_evals.py dry-run condition mismatch: {sorted(conditions)}")
    return len(rows)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    repository_root = root.parents[1] if root.parent.name == "skills" else root
    errors: list[str] = []
    warnings: list[str] = []

    for relative in REQUIRED_PATHS:
        if not (root / relative).is_file():
            errors.append(f"required file missing: {relative}")
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        for item in errors:
            print(f"ERROR: {item}", file=sys.stderr)
        return 1

    try:
        text = skill_md.read_text(encoding="utf-8")
        frontmatter, metadata = parse_frontmatter(text)
    except (UnicodeDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    name = frontmatter.get("name", "")
    description = frontmatter.get("description", "")
    version = metadata.get("version", "")
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
    if not version:
        errors.append("metadata.version is required")

    line_count = len(text.splitlines())
    if line_count > 500:
        warnings.append(f"SKILL.md has {line_count} lines; recommended maximum is 500")

    links = validate_markdown_links(root, repository_root, errors)
    cases = load_reasoning_cases(root / "evals" / "reasoning-cases.jsonl", errors)
    triggers = validate_trigger_cases(root / "evals" / "trigger-cases.csv", errors)
    validate_grading_schema(root / "evals" / "grading-schema.json", errors)
    scripts = validate_python_scripts(root / "scripts", errors)

    agent_text = (root / "agents" / "openai.yaml").read_text(encoding="utf-8")
    for marker in ("default_prompt:", "allow_implicit_invocation: true"):
        if marker not in agent_text:
            errors.append(f"agents/openai.yaml missing marker: {marker}")

    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() not in {
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pyc"
        }:
            try:
                path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                warnings.append(f"non-UTF-8 file: {path.relative_to(root)}")

    if root.parent.name == "skills" and (repository_root / "SKILL.md").exists():
        errors.append("duplicate root SKILL.md found; keep skills/feynman-thinking as the single source of truth")

    dry_runs = validate_dry_run(root, str(cases[0]["id"]), errors) if cases else 0

    for item in warnings:
        print(f"WARNING: {item}")
    if errors:
        for item in errors:
            print(f"ERROR: {item}", file=sys.stderr)
        print(f"Validation failed: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1

    file_count = sum(
        1
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix.lower() != ".pyc"
    )
    print(f"Validation passed: {name} v{version}")
    print(f"Description characters: {len(description)}")
    print(f"SKILL.md lines: {line_count}")
    print(f"Files: {file_count}")
    print(f"Local Markdown links checked: {links}")
    print(f"Trigger cases: {triggers}")
    print(f"Reasoning cases: {len(cases)}")
    print(f"Python scripts compiled without artifacts: {scripts}")
    print(f"Dry-run jobs assembled: {dry_runs}")
    print("Behavioral performance is not proven by this static check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
