#!/usr/bin/env python3
"""Validate this Agent Skill using only Python's standard library.

Checks the most important requirements from the open Agent Skills format:
- SKILL.md exists and starts with YAML frontmatter
- required name and description fields exist
- name matches the parent directory and naming constraints
- description length is valid
- Markdown-linked local files exist
- SKILL.md remains below the recommended 500-line ceiling

This is a lightweight local check, not a replacement for the official
`skills-ref validate` command when that tool is available.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value[0:1] in {'"', "'"}:
        try:
            parsed = ast.literal_eval(value)
            return str(parsed)
        except (SyntaxError, ValueError):
            return value.strip('"\'')
    return value


def parse_top_level_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with '---' YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("YAML frontmatter closing delimiter not found")
    block = text[4:end]
    result: dict[str, str] = {}
    for line in block.splitlines():
        if not line or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = scalar(value)
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    skill_md = root / "SKILL.md"
    errors: list[str] = []
    warnings: list[str] = []

    if not skill_md.is_file():
        print("ERROR: SKILL.md not found", file=sys.stderr)
        return 1

    try:
        text = skill_md.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(f"ERROR: SKILL.md is not valid UTF-8: {exc}", file=sys.stderr)
        return 1

    try:
        fm = parse_top_level_frontmatter(text)
    except ValueError as exc:
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

    for rel in ["agents/openai.yaml", "references", "assets", "scripts"]:
        if not (root / rel).exists():
            warnings.append(f"optional package component missing: {rel}")

    for p in root.rglob("*"):
        if p.is_file():
            try:
                p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                if p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}:
                    warnings.append(f"non-UTF-8 file: {p.relative_to(root)}")

    if errors:
        for item in errors:
            print(f"ERROR: {item}", file=sys.stderr)
    for item in warnings:
        print(f"WARNING: {item}")

    if errors:
        print(f"Validation failed: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1

    print(f"Validation passed: {name}")
    print(f"Description characters: {len(description)}")
    print(f"SKILL.md lines: {line_count}")
    print(f"Files: {sum(1 for p in root.rglob('*') if p.is_file())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
