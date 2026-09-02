#!/usr/bin/env python3
"""Run held-out reasoning cases under baseline, generic, explicit-skill, and implicit-skill conditions.

The harness creates an isolated temporary Git repository for every run. It
installs this skill only for skill conditions, passes the complete task through
standard input to ``codex exec -``, and stores the JSONL trace plus final
response. Evaluator-only expectations never enter the candidate prompt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CONDITIONS = ("baseline", "generic", "feynman", "feynman-implicit")
SKILL_CONDITIONS = {"feynman", "feynman-implicit"}
GENERIC_INSTRUCTION = (
    "다음 과제를 일반적인 비판적 사고 방식으로 수행하라. 주장과 근거를 구분하고, "
    "대안 설명·불확실성·실행 가능한 다음 단계를 검토하라. 특정 인물의 방법론이나 "
    "설치된 스킬을 명시적으로 호출하지 마라."
)
FEYNMAN_INSTRUCTION = (
    "$feynman-thinking을 사용해 다음 과제를 수행하라. 템플릿 제목을 채우는 데 그치지 말고, "
    "가능한 직접 계산·경계 사례·자료 비교·실행 검사를 실제로 수행하라. 불리한 결과가 나오면 "
    "전제·모델·범위와 결론을 수정하라."
)
GENERATED_PARTS = {"__pycache__", "results", "review", "blind-review"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--conditions",
        nargs="+",
        choices=CONDITIONS,
        default=list(CONDITIONS),
        help="Comparison conditions to run.",
    )
    parser.add_argument("--repeats", type=int, default=1, help="Runs per case and condition.")
    parser.add_argument("--case", action="append", dest="case_ids", help="Run only this case id; repeatable.")
    parser.add_argument("--codex", default="codex", help="Codex CLI executable.")
    parser.add_argument("--model", help="Optional model passed to codex exec with --model.")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds allowed per run.")
    parser.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write"),
        default="read-only",
        help="Sandbox mode for the isolated evaluation workspace.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Result directory. Defaults to evals/results/<UTC timestamp>-<suffix>.",
    )
    parser.add_argument(
        "--keep-workspaces",
        action="store_true",
        help="Copy isolated workspaces into the run directory for debugging.",
    )
    parser.add_argument("--shuffle", action="store_true", help="Randomize case/condition execution order.")
    parser.add_argument("--seed", type=int, default=20260902, help="Seed used with --shuffle.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the assembled job matrix as JSONL without requiring Codex credentials.",
    )
    return parser.parse_args()


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(case, dict):
            raise ValueError(f"{path}:{line_no}: case must be a JSON object")
        case_id = str(case.get("id", "")).strip()
        prompt = str(case.get("prompt", "")).strip()
        if not case_id or not prompt:
            raise ValueError(f"{path}:{line_no}: id and prompt are required")
        if case_id in seen:
            raise ValueError(f"{path}:{line_no}: duplicate id {case_id}")
        seen.add(case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"no cases found in {path}")
    return cases


def select_cases(cases: list[dict[str, Any]], requested: list[str] | None) -> list[dict[str, Any]]:
    if not requested:
        return cases
    index = {str(case["id"]): case for case in cases}
    unknown = sorted(set(requested) - set(index))
    if unknown:
        raise ValueError(f"unknown case ids: {', '.join(unknown)}")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case_id in requested:
        if case_id not in seen:
            result.append(index[case_id])
            seen.add(case_id)
    return result


def make_prompt(condition: str, task: str) -> str:
    if condition in {"baseline", "feynman-implicit"}:
        return task
    if condition == "generic":
        return f"{GENERIC_INSTRUCTION}\n\n사용자 과제:\n{task}"
    if condition == "feynman":
        return f"{FEYNMAN_INSTRUCTION}\n\n사용자 과제:\n{task}"
    raise ValueError(f"unknown condition: {condition}")


def install_skill(skill_root: Path, workspace: Path) -> None:
    target = workspace / ".agents" / "skills" / skill_root.name

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in GENERATED_PARTS or name.endswith((".pyc", ".pyo"))
        }

    shutil.copytree(skill_root, target, ignore=ignore)


def decode_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


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


def parse_trace(stdout: str) -> tuple[str, dict[str, Any], dict[str, int]]:
    final_message = ""
    usage: dict[str, Any] = {}
    counts = {
        "command_execution": 0,
        "file_change": 0,
        "mcp_tool_call": 0,
        "web_search": 0,
        "agent_message": 0,
        "skill_reference_event": 0,
        "invalid_json_line": 0,
    }
    skill_markers = (".agents/skills/feynman-thinking", "feynman-thinking/skill.md")
    for raw in stdout.splitlines():
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            counts["invalid_json_line"] += 1
            continue
        serialized = json.dumps(event, ensure_ascii=False).casefold().replace("\\", "/")
        if any(marker in serialized for marker in skill_markers):
            counts["skill_reference_event"] += 1
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if item_type in counts:
            counts[item_type] += 1
        if item_type in {"agent_message", "assistant_message", "message"}:
            text = text_value(item)
            if text:
                final_message = text
    return final_message, usage, counts


def run_command(command: list[str], *, cwd: Path | None = None, timeout: int = 15) -> str | None:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    text = (completed.stdout or completed.stderr or "").strip()
    return text or None


def repository_commit(skill_root: Path) -> str | None:
    repository = skill_root.parents[1]
    return run_command(["git", "rev-parse", "HEAD"], cwd=repository)


def skill_version(skill_root: Path) -> str | None:
    text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"(?m)^\s{2}version:\s*[\"']?([^\"'\n]+)", text)
    return match.group(1).strip() if match else None


def skill_digest(skill_root: Path) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in skill_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(skill_root)
        if any(part in GENERATED_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        files.append(path)
    for path in sorted(files, key=lambda item: item.relative_to(skill_root).as_posix()):
        relative = path.relative_to(skill_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def build_jobs(
    cases: Iterable[dict[str, Any]], conditions: Iterable[str], repeats: int
) -> list[tuple[dict[str, Any], str, int]]:
    return [
        (case, condition, repeat)
        for case in cases
        for condition in conditions
        for repeat in range(1, repeats + 1)
    ]


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def dry_run(jobs: list[tuple[dict[str, Any], str, int]]) -> int:
    for ordinal, (case, condition, repeat) in enumerate(jobs, 1):
        prompt = make_prompt(condition, str(case["prompt"]))
        print(
            json.dumps(
                {
                    "ordinal": ordinal,
                    "case_id": case["id"],
                    "condition": condition,
                    "repeat": repeat,
                    "skill_installed": condition in SKILL_CONDITIONS,
                    "explicit_skill_invocation": condition == "feynman",
                    "prompt_sha256": prompt_digest(prompt),
                    "prompt": prompt,
                },
                ensure_ascii=False,
            )
        )
    return 0


def run_one(
    *,
    codex: str,
    model: str | None,
    timeout: int,
    sandbox: str,
    skill_root: Path,
    case: dict[str, Any],
    condition: str,
    repeat: int,
    run_dir: Path,
    keep_workspace: bool,
) -> dict[str, Any]:
    run_id = f"{case['id']}__{condition}__r{repeat}"
    artifact_dir = run_dir / "artifacts" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    prompt = make_prompt(condition, str(case["prompt"]))
    skill_installed = condition in SKILL_CONDITIONS

    started = utc_now()
    with tempfile.TemporaryDirectory(prefix="feynman-eval-") as tmp:
        workspace = Path(tmp)
        subprocess.run(
            ["git", "init", "-q"],
            cwd=workspace,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if skill_installed:
            install_skill(skill_root, workspace)

        command = [
            codex,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            sandbox,
        ]
        if model:
            command.extend(["--model", model])
        command.append("-")

        env = os.environ.copy()
        env.setdefault("NO_COLOR", "1")
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            exit_code = completed.returncode
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            timed_out = False
            harness_error = None
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = decode_stream(exc.stdout)
            stderr = decode_stream(exc.stderr) + f"\nTimed out after {timeout} seconds."
            timed_out = True
            harness_error = None

        finished = utc_now()
        final_message, usage, event_counts = parse_trace(stdout)
        (artifact_dir / "trace.jsonl").write_text(stdout, encoding="utf-8")
        (artifact_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
        (artifact_dir / "final.md").write_text(final_message, encoding="utf-8")
        (artifact_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        if keep_workspace:
            shutil.copytree(workspace, artifact_dir / "workspace", dirs_exist_ok=True)

    return {
        "run_id": run_id,
        "case_id": case["id"],
        "condition": condition,
        "repeat": repeat,
        "skill_installed": skill_installed,
        "explicit_skill_invocation": condition == "feynman",
        "prompt_sha256": prompt_digest(prompt),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "harness_error": harness_error,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": (finished - started).total_seconds(),
        "usage": usage,
        "event_counts": event_counts,
        "final_message_present": bool(final_message.strip()),
        "artifact_dir": str(artifact_dir.relative_to(run_dir)),
    }


def main() -> int:
    args = parse_args()
    if args.repeats < 1:
        raise SystemExit("--repeats must be at least 1")
    if args.timeout < 1:
        raise SystemExit("--timeout must be at least 1 second")

    skill_root = Path(__file__).resolve().parents[1]
    cases = select_cases(load_cases(skill_root / "evals" / "reasoning-cases.jsonl"), args.case_ids)
    jobs = build_jobs(cases, args.conditions, args.repeats)
    if args.shuffle:
        random.Random(args.seed).shuffle(jobs)
    if args.dry_run:
        return dry_run(jobs)

    if shutil.which(args.codex) is None:
        raise SystemExit(f"Codex CLI not found: {args.codex}")
    if shutil.which("git") is None:
        raise SystemExit("git executable not found")

    timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    default_name = f"{timestamp}-{uuid.uuid4().hex[:8]}"
    run_dir = args.output_dir or (skill_root / "evals" / "results" / default_name)
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=False)

    metadata: dict[str, Any] = {
        "schema_version": 2,
        "created_at": utc_now().isoformat(),
        "skill_version": skill_version(skill_root),
        "skill_package_sha256": skill_digest(skill_root),
        "repository_commit": repository_commit(skill_root),
        "conditions": args.conditions,
        "repeats": args.repeats,
        "model": args.model,
        "codex_executable": args.codex,
        "codex_version": run_command([args.codex, "--version"]),
        "sandbox": args.sandbox,
        "shuffle": args.shuffle,
        "seed": args.seed if args.shuffle else None,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cases": [case["id"] for case in cases],
        "job_order": [
            {"case_id": case["id"], "condition": condition, "repeat": repeat}
            for case, condition, repeat in jobs
        ],
        "runs": [],
    }
    metadata_path = run_dir / "run-metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    total = len(jobs)
    for index, (case, condition, repeat) in enumerate(jobs, 1):
        print(f"[{index}/{total}] {case['id']} / {condition} / r{repeat}", flush=True)
        try:
            result = run_one(
                codex=args.codex,
                model=args.model,
                timeout=args.timeout,
                sandbox=args.sandbox,
                skill_root=skill_root,
                case=case,
                condition=condition,
                repeat=repeat,
                run_dir=run_dir,
                keep_workspace=args.keep_workspaces,
            )
        except Exception as exc:  # preserve harness failure as evidence and continue
            result = {
                "run_id": f"{case['id']}__{condition}__r{repeat}",
                "case_id": case["id"],
                "condition": condition,
                "repeat": repeat,
                "skill_installed": condition in SKILL_CONDITIONS,
                "explicit_skill_invocation": condition == "feynman",
                "exit_code": 1,
                "timed_out": False,
                "harness_error": f"{type(exc).__name__}: {exc}",
                "final_message_present": False,
            }
        metadata["runs"].append(result)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    failures = [
        run
        for run in metadata["runs"]
        if run.get("exit_code") != 0 or not run.get("final_message_present")
    ]
    print(f"Run artifacts: {run_dir}")
    print(f"Completed: {len(metadata['runs'])}; execution failures: {len(failures)}")
    print("Next: python scripts/grade_evals.py --run-dir <run-artifact-directory>")
    print("Human audit: python scripts/prepare_blind_review.py --run-dir <run-artifact-directory>")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
