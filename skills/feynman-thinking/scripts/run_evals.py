#!/usr/bin/env python3
"""Run held-out reasoning cases under baseline, generic, and skill conditions.

The harness creates an isolated temporary Git repository for each run, installs
this skill only for the feynman condition, invokes `codex exec --json`, and
stores the JSONL trace plus final response. It deliberately keeps expected
findings out of the task prompt.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONDITIONS = ("baseline", "generic", "feynman")
GENERIC_INSTRUCTION = (
    "다음 과제를 일반적인 비판적 사고 방식으로 수행하라. 주장과 근거를 구분하고, "
    "대안 설명·불확실성·실행 가능한 다음 단계를 검토하라. 특정 인물의 방법론이나 "
    "설치된 스킬을 사용하지 마라."
)
FEYNMAN_INSTRUCTION = (
    "$feynman-thinking을 사용해 다음 과제를 수행하라. 템플릿 제목을 채우는 데 그치지 말고, "
    "가능한 직접 계산·경계 사례·자료 비교·실행 검사를 실제로 수행하라. 불리한 결과가 나오면 "
    "전제·모델·범위와 결론을 수정하라."
)


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
        "--output-dir",
        type=Path,
        help="Result directory. Defaults to evals/results/<UTC timestamp>.",
    )
    parser.add_argument(
        "--keep-workspaces",
        action="store_true",
        help="Copy isolated workspaces into the run directory for debugging.",
    )
    return parser.parse_args()


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        cases.append(case)
    return cases


def make_prompt(condition: str, task: str) -> str:
    if condition == "baseline":
        return task
    if condition == "generic":
        return f"{GENERIC_INSTRUCTION}\n\n사용자 과제:\n{task}"
    if condition == "feynman":
        return f"{FEYNMAN_INSTRUCTION}\n\n사용자 과제:\n{task}"
    raise ValueError(f"unknown condition: {condition}")


def install_skill(skill_root: Path, workspace: Path) -> None:
    target = workspace / ".agents" / "skills" / skill_root.name

    def ignore(_directory: str, names: list[str]) -> set[str]:
        ignored = {"__pycache__"}
        if "results" in names:
            ignored.add("results")
        return ignored

    shutil.copytree(skill_root, target, ignore=ignore)


def parse_trace(stdout: str) -> tuple[str, dict[str, Any], dict[str, int]]:
    final_message = ""
    usage: dict[str, Any] = {}
    counts = {
        "command_execution": 0,
        "file_change": 0,
        "mcp_tool_call": 0,
        "web_search": 0,
        "agent_message": 0,
    }
    for raw in stdout.splitlines():
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in counts and event.get("type") in {"item.started", "item.completed"}:
            if event.get("type") == "item.completed" or item_type == "agent_message":
                counts[item_type] += 1
        if event.get("type") == "item.completed" and item_type == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                final_message = text
    return final_message, usage, counts


def run_one(
    *,
    codex: str,
    model: str | None,
    timeout: int,
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
        if condition == "feynman":
            install_skill(skill_root, workspace)

        prompt = make_prompt(condition, str(case["prompt"]))
        command = [
            codex,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            "read-only",
        ]
        if model:
            command.extend(["--model", model])
        command.append(prompt)

        env = os.environ.copy()
        env.setdefault("NO_COLOR", "1")
        started = datetime.now(timezone.utc)
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = exc.stdout or ""
            stderr = (exc.stderr or "") + f"\nTimed out after {timeout} seconds."
            timed_out = True
        finished = datetime.now(timezone.utc)

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
        "exit_code": exit_code,
        "timed_out": timed_out,
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
    if shutil.which(args.codex) is None:
        raise SystemExit(f"Codex CLI not found: {args.codex}")
    if shutil.which("git") is None:
        raise SystemExit("git executable not found")

    skill_root = Path(__file__).resolve().parents[1]
    cases = load_cases(skill_root / "evals" / "reasoning-cases.jsonl")
    if args.case_ids:
        requested = set(args.case_ids)
        known = {case["id"] for case in cases}
        unknown = requested - known
        if unknown:
            raise SystemExit(f"Unknown case ids: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case["id"] in requested]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.output_dir or (skill_root / "evals" / "results" / timestamp)
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=False)

    metadata: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "skill_version": "0.4.0",
        "skill_root": str(skill_root),
        "conditions": args.conditions,
        "repeats": args.repeats,
        "model": args.model,
        "codex_executable": args.codex,
        "cases": [case["id"] for case in cases],
        "runs": [],
    }

    total = len(cases) * len(args.conditions) * args.repeats
    completed_count = 0
    for case in cases:
        for condition in args.conditions:
            for repeat in range(1, args.repeats + 1):
                completed_count += 1
                print(f"[{completed_count}/{total}] {case['id']} / {condition} / r{repeat}", flush=True)
                try:
                    result = run_one(
                        codex=args.codex,
                        model=args.model,
                        timeout=args.timeout,
                        skill_root=skill_root,
                        case=case,
                        condition=condition,
                        repeat=repeat,
                        run_dir=run_dir,
                        keep_workspace=args.keep_workspaces,
                    )
                except Exception as exc:
                    result = {
                        "run_id": f"{case['id']}__{condition}__r{repeat}",
                        "case_id": case["id"],
                        "condition": condition,
                        "repeat": repeat,
                        "exit_code": 1,
                        "harness_error": f"{type(exc).__name__}: {exc}",
                        "final_message_present": False,
                    }
                metadata["runs"].append(result)
                (run_dir / "run-metadata.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
                )

    failures = [run for run in metadata["runs"] if run.get("exit_code") != 0 or not run.get("final_message_present")]
    print(f"Run artifacts: {run_dir}")
    print(f"Completed: {len(metadata['runs'])}; execution failures: {len(failures)}")
    print("Next: python scripts/grade_evals.py --run-dir <run-artifact-directory>")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
