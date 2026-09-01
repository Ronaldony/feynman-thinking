# 평가 자산 사용법

이 디렉터리는 `feynman-thinking`이 실제로 AI의 작업 행동과 결과를 바꾸는지 비교하기 위한 자료다.

## 파일

- `trigger-cases.csv`: 스킬 발동 기대 사례
- `reasoning-cases.jsonl`: 도메인별 추론 과제와 평가자가 볼 기대 행동
- `grading-rubric.json`: 0/1/2/N/A 채점 기준과 하드 게이트
- `results/`: 실행 결과 JSONL 저장 위치
- `review/`: 조건 블라인드 검토 패킷 저장 위치

`expected_behaviors`, `forbidden_shortcuts`, `applicable_dimensions`는 평가용 메타데이터다. `run_eval.py`는 모델에게 `prompt`만 전달한다.

## 조건 분리

| 조건 | 스킬 환경 | 프롬프트 |
|---|---|---|
| baseline | 없음 또는 비활성 | 원문 |
| generic | 없음 또는 비활성 | 일반 비판적 사고 지침 + 원문 |
| feynman-explicit | 설치·활성 | `$feynman-thinking` 명시 지침 + 원문 |
| feynman-implicit | 설치·활성 | 원문 |

`baseline`과 `feynman-implicit`는 프롬프트가 같으므로 실행 환경을 반드시 분리한다. 실행 trace에서 스킬 로드 여부를 확인한다.

## 프롬프트 조립 확인

```bash
python scripts/run_eval.py --condition all --dry-run
```

## Codex CLI 실행 예

```bash
python scripts/run_eval.py \
  --condition feynman-explicit \
  --repeats 3 \
  --command "codex exec --json -" \
  --environment "Codex CLI, feynman-thinking installed"
```

조건별로 다른 설치 환경이나 명령을 사용해야 하면 각 조건을 별도 실행 파일로 저장한다.

## 블라인드 검토

```bash
python scripts/prepare_blind_review.py \
  --input evals/results/run.jsonl \
  --output-dir evals/review/run
```

채점 완료 전 `condition-key.json`을 평가자에게 공개하지 않는다. 섹션 제목과 키워드의 존재가 아니라 실제 유도·계산·실행, 경쟁 모델의 설명력, 검사의 판별력과 수정 효과를 평가한다.

평가 설계와 성공 기준은 [../references/evaluation.md](../references/evaluation.md)를 참조한다.
