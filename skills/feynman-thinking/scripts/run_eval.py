#!/usr/bin/env python3
"""Run behavioral comparisons for the feynman-thinking Agent Skill.

Each prompt is passed to an external command through standard input. Evaluator
metadata stays outside the model prompt, and stdout/stderr plus the extracted
final answer are stored as JSONL evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONDITIONS = ("baseline", "generic", "feynman-explicit", "feynman-implicit")
GENERIC_PREFIX = """다음 과제를 일반적인 비판적 사고 방식으로 검토하라.
사실과 가정을 구분하고 대안 설명을 고려하며, 가능한 계산이나 검사를 수행한 뒤 결론의 한계와 실행 가능한 다음 단계를 제시하라."""
FEYNMAN_PREFIX = """$feynman-thinking {level}를 사용해 다음 과제를 수행하라.
스킬의 핵심 추론 루프를 실제 작업에 적용하고 가능한 계산·추정·자료 확인·테스트를 수행하라. 수행하지 못한 검사는 미검증으로 표시하고, 불리한 결과가 모델·범위·확신 또는 결론을 어떻게 바꾸는지 보여라."""
ENVIRONMENT_REQUIREMENTS = {
    "baseline": "feynman-thinking이 설치되지 않았거나 비활성화된 환경",
    "generic": "feynman-thinking이 설치되지 않았거나 비활성화된 환경",
    "feynman-explicit": "feynman-thinking이 설치·활성화된 환경",
    "feynman-implicit": "feynman-thinking이 설치·활성화된 환경",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def locate_skill() -> Path:
    script = Path(__file__).resolve()
    for parent in script.parents:
        package = parent / "skills" / "feynman-thinking"
        if (package / "evals" / "reasoning-cases.jsonl").is_file():
            return package
    candidate = script.parents[1]
    if (candidate / "evals" / "reasoning-cases.jsonl").is_file():
        return candidate
    raise RuntimeError("feynman-thinking skill root not found")


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            try:
                case = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(case, dict):
                raise ValueError(f"case at {path}:{line_number} must be an object")
            case_id = str(case.get("id", "")).strip()
            prompt = str(case.get("prompt", "")).strip()
            if not case_id or not prompt:
                raise ValueError(f"case at {path}:{line_number} requires id and prompt")
            if case_id in seen:
                raise ValueError(f"duplicate case id: {case_id}")
            seen.add(case_id)
            cases.append(case)
    if not cases:
        raise ValueError(f"no cases found in {path}")
    return cases


def choose_cases(cases: list[dict[str, Any]], ids: list[str] | None) -> list[dict[str, Any]]:
    if not ids:
        return cases
    index = {str(case["id"]): case for case in cases}
    missing = [case_id for case_id in ids if case_id not in index]
    if missing:
        raise ValueError(f"unknown case id(s): {', '.join(missing)}")
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case_id in ids:
        if case_id not in seen:
            chosen.append(index[case_id])
            seen.add(case_id)
    return chosen


def build_prompt(case: dict[str, Any], condition: str) -> str:
    prompt = str(case["prompt"]).strip()
    if condition in {"baseline", "feynman-implicit"}:
        return prompt
    if condition == "generic":
        return f"{GENERIC_PREFIX}\n\n[과제]\n{prompt}"
    if condition == "feynman-explicit":
        level = str(case.get("level", "L1")).strip() or "L1"
        return f"{FEYNMAN_PREFIX.format(level=level)}\n\n[과제]\n{prompt}"
    raise ValueError(f"unsupported condition: {condition}")


def parse_events(stdout: str) -> list[Any]:
    events: list[Any] = []
    for raw in stdout.splitlines():
        if not raw.strip():
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            return []
    return events


def text_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        parts = [text for item in value if (text := text_value(item))]
        return "\n".join(parts) or None
    if isinstance(value, dict):
        for key in ("text", "output_text", "content"):
            text = text_value(value.get(key))
            if text:
                return text
    return None


def final_message(stdout: str, events: list[Any]) -> str:
    candidates: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict):
            if item.get("type") in {"agent_message", "assistant_message", "message"}:
                text = text_value(item)
                if text:
                    candidates.append(text)
        for key in ("final_output", "final_message", "output_text"):
            text = text_value(event.get(key))
            if text:
                candidates.append(text)
    return candidates[-1] if candidates else stdout.strip()


def decode_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def execute(argv: list[str], prompt: str, timeout: float, cwd: Path | None) -> dict[str, Any]:
    started_at = now()
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            check=False,
        )
        stdout, stderr = result.stdout or "", result.stderr or ""
        status = "completed" if result.returncode == 0 else "failed"
        exit_code: int | None = result.returncode
        error = None
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = decode_stream(exc.stdout), decode_stream(exc.stderr)
        status, exit_code = "timeout", None
        error = f"command exceeded {timeout:g} seconds"
    except OSError as exc:
        stdout, stderr = "", ""
        status, exit_code = "error", None
        error = f"could not start command: {exc}"
    events = parse_events(stdout)
    return {
        "started_at": started_at,
        "duration_seconds": round(time.monotonic() - started, 6),
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "trace_event_count": len(events),
        "final_message": final_message(stdout, events),
        "error": error,
    }


def record(case: dict[str, Any], condition: str, repeat: int, prompt: str, args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:10]}",
        "case_id": str(case["id"]),
        "domain": case.get("domain"),
        "difficulty": case.get("difficulty"),
        "level": case.get("level"),
        "condition": condition,
        "repeat": repeat,
        "environment": args.environment,
        "environment_requirement": ENVIRONMENT_REQUIREMENTS[condition],
        "command": args.command,
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
    }


def parser() -> argparse.ArgumentParser:
    skill = locate_skill()
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--cases", type=Path, default=skill / "evals" / "reasoning-cases.jsonl")
    result.add_argument("--case-id", action="append")
    result.add_argument("--condition", choices=(*CONDITIONS, "all"), default="all")
    result.add_argument("--repeats", type=int, default=1)
    result.add_argument("--command", help="external command reading the complete prompt from stdin")
    result.add_argument("--output", type=Path)
    result.add_argument("--environment", default="")
    result.add_argument("--timeout", type=float, default=300.0)
    result.add_argument("--cwd", type=Path)
    result.add_argument("--shuffle", action="store_true")
    result.add_argument("--seed", type=int, default=20260901)
    result.add_argument("--fail-fast", action="store_true")
    result.add_argument("--dry-run", action="store_true")
    return result


def main() -> int:
    arg_parser = parser()
    args = arg_parser.parse_args()
    if args.repeats < 1:
        arg_parser.error("--repeats must be at least 1")
    if args.timeout <= 0:
        arg_parser.error("--timeout must be positive")
    if not args.dry_run and not args.command:
        arg_parser.error("--command is required unless --dry-run is used")
    cases = choose_cases(load_cases(args.cases.expanduser().resolve()), args.case_id)
    conditions = list(CONDITIONS) if args.condition == "all" else [args.condition]
    jobs = [(case, condition, repeat) for case in cases for condition in conditions for repeat in range(1, args.repeats + 1)]
    if args.shuffle:
        random.Random(args.seed).shuffle(jobs)
    if args.dry_run:
        for case, condition, repeat in jobs:
            prompt = build_prompt(case, condition)
            row = record(case, condition, repeat, prompt, args)
            row["status"] = "dry-run"
            print(json.dumps(row, ensure_ascii=False))
        return 0
    argv = shlex.split(args.command, posix=os.name != "nt")
    if not argv:
        arg_parser.error("--command must not be empty")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or locate_skill() / "evals" / "results" / f"run-{stamp}.jsonl").expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cwd = args.cwd.expanduser().resolve() if args.cwd else None
    failures = executed = 0
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for case, condition, repeat in jobs:
            prompt = build_prompt(case, condition)
            row = record(case, condition, repeat, prompt, args)
            row.update(execute(argv, prompt, args.timeout, cwd))
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            executed += 1
            if row["status"] != "completed":
                failures += 1
                if args.fail_fast:
                    break
    print(f"Wrote {executed} job record(s) to {output}", file=sys.stderr)
    if failures:
        print(f"Evaluation had {failures} failed/timeout job(s)", file=sys.stderr)
        return 1
    print(f"Evaluation completed: {executed} job(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
