#!/usr/bin/env python3
"""Create a condition-blind human review packet from run_evals.py artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
from pathlib import Path
from typing import Any

DIMENSIONS = (
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
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True, help="Directory created by run_evals.py.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Destination. Defaults to <run-dir>/blind-review.",
    )
    parser.add_argument("--seed", type=int, default=20260902, help="Sample randomization seed.")
    parser.add_argument("--force", action="store_true", help="Replace a non-empty output directory.")
    return parser.parse_args()


def load_cases(path: Path) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        case_id = str(case.get("id", "")).strip()
        if not case_id or case_id in cases:
            raise ValueError(f"{path}:{line_number}: missing or duplicate case id {case_id!r}")
        cases[case_id] = case
    return cases


def prepare_directory(path: Path, force: bool) -> None:
    if path.exists() and any(path.iterdir()):
        if not force:
            raise FileExistsError(f"output directory is not empty: {path}; use --force")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def read_candidate(run_dir: Path, run: dict[str, Any]) -> str:
    artifact = run.get("artifact_dir")
    if not artifact:
        return ""
    path = run_dir / str(artifact) / "final.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def main() -> int:
    args = parse_args()
    run_dir = args.run_dir.expanduser().resolve()
    metadata_path = run_dir / "run-metadata.json"
    if not metadata_path.is_file():
        raise SystemExit(f"run metadata not found: {metadata_path}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    runs = metadata.get("runs")
    if not isinstance(runs, list) or not runs:
        raise SystemExit("run metadata contains no runs")

    skill_root = Path(__file__).resolve().parents[1]
    cases = load_cases(skill_root / "evals" / "reasoning-cases.jsonl")
    output = (args.output_dir or (run_dir / "blind-review")).expanduser().resolve()
    prepare_directory(output, args.force)

    ordered = list(runs)
    random.Random(args.seed).shuffle(ordered)
    samples: list[dict[str, Any]] = []
    key: list[dict[str, Any]] = []
    score_rows: list[dict[str, str]] = []

    for number, run in enumerate(ordered, 1):
        case_id = str(run.get("case_id", ""))
        if case_id not in cases:
            raise ValueError(f"run references unknown case id: {case_id}")
        case = cases[case_id]
        sample_id = f"S{number:04d}"
        applicable = set(case.get("required_behaviors", [])) | set(case.get("optional_behaviors", []))
        unknown = applicable - set(DIMENSIONS)
        if unknown:
            raise ValueError(f"case {case_id} uses unknown dimensions: {sorted(unknown)}")

        samples.append(
            {
                "sample_id": sample_id,
                "case_id": case_id,
                "domain": case.get("domain"),
                "risk_level": case.get("risk_level"),
                "prompt": case.get("prompt"),
                "candidate": read_candidate(run_dir, run),
                "execution": {
                    "exit_code": run.get("exit_code"),
                    "timed_out": run.get("timed_out"),
                    "harness_error": run.get("harness_error"),
                    "event_counts": run.get("event_counts", {}),
                    "usage": run.get("usage", {}),
                },
                "expected_findings": case.get("expected_findings", []),
                "required_behaviors": case.get("required_behaviors", []),
                "optional_behaviors": case.get("optional_behaviors", []),
                "hard_failures": case.get("hard_failures", []),
            }
        )
        key.append(
            {
                "sample_id": sample_id,
                "run_id": run.get("run_id"),
                "case_id": case_id,
                "condition": run.get("condition"),
                "repeat": run.get("repeat"),
                "skill_installed": run.get("skill_installed"),
                "explicit_skill_invocation": run.get("explicit_skill_invocation"),
            }
        )
        row: dict[str, str] = {
            "sample_id": sample_id,
            "case_id": case_id,
            "hard_failures": "",
            "expected_finding_hit_rate": "",
            "overall_notes": "",
        }
        for dimension in DIMENSIONS:
            row[f"score_{dimension}"] = "" if dimension in applicable else "NA"
        score_rows.append(row)

    with (output / "samples.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
    (output / "condition-key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    fields = ["sample_id", "case_id"]
    fields.extend(f"score_{dimension}" for dimension in DIMENSIONS)
    fields.extend(["hard_failures", "expected_finding_hit_rate", "overall_notes"])
    with (output / "scoring.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(score_rows)

    guide = """# 조건 블라인드 검토 안내

`condition-key.json`은 채점이 끝날 때까지 열지 않는다.

1. `samples.jsonl`의 후보 답변을 과제·기대 발견·행동 기준에 따라 검토한다.
2. 적용 가능한 차원은 `0 / 1 / 2`, 적용되지 않는 차원은 `NA`로 채점한다.
3. 제목, 태그, 파인만이라는 이름만으로 점수를 주지 않는다.
4. 검사 제안은 실제 수행과 구분한다. 실행할 수 없었던 경우에는 미검증 상태와 결과별 결론 변경 조건을 본다.
5. 경쟁 모델은 같은 관찰을 설명할 수 있어야 하며, 판별 검사는 결과에 따라 모델 선택이나 결론을 바꿔야 한다.
6. 하드 실패는 `hard_failures` 열에 세미콜론으로 구분해 기록한다.
7. 채점 후에만 조건 키를 열어 baseline / generic / explicit / implicit 결과를 비교한다.

차원 정의는 `references/evaluation.md`의 결과 품질 루브릭을 따른다. 모델 보조 채점과 사람 채점이 충돌하면 개별 trace와 계산을 다시 검토한다.
"""
    (output / "REVIEW.md").write_text(guide, encoding="utf-8")
    print(f"Prepared {len(samples)} condition-blind sample(s) in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
