#!/usr/bin/env python3
"""Create condition-blind review packets from behavioral evaluation JSONL."""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
from pathlib import Path
from typing import Any


def locate_skill() -> Path:
    script = Path(__file__).resolve()
    for parent in script.parents:
        package = parent / "skills" / "feynman-thinking"
        if (package / "evals" / "grading-rubric.json").is_file():
            return package
    candidate = script.parents[1]
    if (candidate / "evals" / "grading-rubric.json").is_file():
        return candidate
    raise RuntimeError("feynman-thinking skill root not found")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def load_jsonl(paths: list[Path], annotate_source: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"row at {path}:{line_number} must be an object")
                if annotate_source:
                    row = dict(row)
                    row["_source_input"] = str(path)
                rows.append(row)
    return rows


def case_index(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for case in load_jsonl([path]):
        case_id = str(case.get("id", "")).strip()
        if not case_id or case_id in result:
            raise ValueError(f"missing or duplicate case id: {case_id!r}")
        result[case_id] = case
    return result


def prepare_directory(path: Path, force: bool) -> None:
    if path.exists() and any(path.iterdir()):
        if not force:
            raise FileExistsError(f"output directory is not empty: {path}; use --force")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def parser() -> argparse.ArgumentParser:
    skill = locate_skill()
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--input", type=Path, action="append", required=True)
    result.add_argument("--cases", type=Path, default=skill / "evals" / "reasoning-cases.jsonl")
    result.add_argument("--rubric", type=Path, default=skill / "evals" / "grading-rubric.json")
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--seed", type=int, default=20260901)
    result.add_argument("--force", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    inputs = [path.expanduser().resolve() for path in args.input]
    cases = case_index(args.cases.expanduser().resolve())
    rubric = load_json(args.rubric.expanduser().resolve())
    runs = load_jsonl(inputs, annotate_source=True)
    if not runs:
        raise ValueError("no run records found")
    dimensions = [str(item["id"]) for item in rubric.get("dimensions", [])]
    if not dimensions:
        raise ValueError("rubric has no dimensions")
    for row in runs:
        case_id = str(row.get("case_id", ""))
        if case_id not in cases:
            raise ValueError(f"run references unknown case id: {case_id}")
        if not row.get("condition"):
            raise ValueError(f"run {row.get('run_id', '<unknown>')} has no condition")

    random.Random(args.seed).shuffle(runs)
    output = args.output_dir.expanduser().resolve()
    prepare_directory(output, args.force)
    samples: list[dict[str, Any]] = []
    key: list[dict[str, Any]] = []
    score_rows: list[dict[str, str]] = []

    for number, row in enumerate(runs, 1):
        sample_id = f"S{number:04d}"
        case_id = str(row["case_id"])
        case = cases[case_id]
        applicable = {str(item) for item in case.get("applicable_dimensions", [])}
        samples.append(
            {
                "sample_id": sample_id,
                "case_id": case_id,
                "domain": case.get("domain"),
                "difficulty": case.get("difficulty"),
                "level": case.get("level"),
                "prompt": case.get("prompt"),
                "response": row.get("final_message", ""),
                "execution_status": row.get("status"),
                "execution_error": row.get("error"),
                "expected_behaviors": case.get("expected_behaviors", []),
                "forbidden_shortcuts": case.get("forbidden_shortcuts", []),
                "applicable_dimensions": sorted(applicable),
            }
        )
        key.append(
            {
                "sample_id": sample_id,
                "run_id": row.get("run_id"),
                "case_id": case_id,
                "condition": row.get("condition"),
                "repeat": row.get("repeat"),
                "environment": row.get("environment"),
                "command": row.get("command"),
                "prompt_sha256": row.get("prompt_sha256"),
                "source_input": row.get("_source_input"),
            }
        )
        score: dict[str, str] = {
            "sample_id": sample_id,
            "case_id": case_id,
            "execution_status": str(row.get("status", "")),
            "hard_gate_failures": "",
            "overall_notes": "",
        }
        for dimension in dimensions:
            score[f"score_{dimension}"] = "" if dimension in applicable else "N/A"
        score_rows.append(score)

    with (output / "samples.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
    with (output / "condition-key.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(key, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with (output / "rubric.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(rubric, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    fieldnames = ["sample_id", "case_id", "execution_status"]
    fieldnames.extend(f"score_{dimension}" for dimension in dimensions)
    fieldnames.extend(["hard_gate_failures", "overall_notes"])
    with (output / "scoring.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(score_rows)

    review_text = """# 블라인드 평가 안내

1. `samples.jsonl`의 각 응답을 `rubric.json` 기준으로 평가한다.
2. 적용 가능한 차원만 0/1/2로 채점하고 나머지는 N/A로 유지한다.
3. 하드 게이트 위반은 쉼표로 구분해 `hard_gate_failures`에 기록한다.
4. 제목과 키워드가 아니라 실제 유도·계산·실행, 경쟁 모델의 설명력, 검사의 판별력과 수정 효과를 본다.
5. 채점이 끝날 때까지 `condition-key.json`을 열지 않는다.
"""
    (output / "REVIEW.md").write_text(review_text, encoding="utf-8")
    print(f"Prepared {len(samples)} blind sample(s) in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
