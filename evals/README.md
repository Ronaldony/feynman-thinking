# 평가 자산

이 디렉터리는 `feynman-thinking`이 실제로 AI의 작업 행동과 결과를 바꾸는지 비교하기 위한 held-out 자료와 채점 Schema를 보관한다.

## 파일

- `trigger-cases.csv`: 명시·암묵·인접·비발동 요청의 기대 발동값
- `reasoning-cases.jsonl`: 도메인별 과제, 기대 발견, 필수·선택 행동과 하드 실패
- `grading-schema.json`: 모델 보조 채점기의 구조화 출력 JSON Schema
- `results/`: 실행 결과 저장 위치이며 Git에서 제외됨

`expected_findings`, `required_behaviors`, `optional_behaviors`, `hard_failures`, `notes`는 평가자 전용 메타데이터다. 후보 모델에는 `prompt`만 전달한다.

## 네 조건

| 조건 | 스킬 설치 | 후보 프롬프트 |
|---|---:|---|
| `baseline` | 아니요 | 원문 과제 |
| `generic` | 아니요 | 일반 비판적 사고 지침 + 과제 |
| `feynman` | 예 | `$feynman-thinking` 명시 호출 + 과제 |
| `feynman-implicit` | 예 | 원문 과제 |

`baseline`과 `feynman-implicit`는 후보에게 전달되는 원문이 같다. 실행 환경과 `skill_installed` 기록을 분리해야 한다.

## 프롬프트 조립 확인

```bash
python ../scripts/run_evals.py \
  --case software-perf-01 \
  --conditions baseline generic feynman feynman-implicit \
  --dry-run
```

## 반복 실행

```bash
python ../scripts/run_evals.py \
  --conditions baseline generic feynman feynman-implicit \
  --repeats 3 \
  --shuffle \
  --seed 20260902
```

## 모델 보조 채점

```bash
python ../scripts/grade_evals.py --run-dir results/<run-id>
```

## 조건 블라인드 사람 검토

```bash
python ../scripts/prepare_blind_review.py --run-dir results/<run-id>
```

채점 완료 전 생성된 `condition-key.json`을 열지 않는다. 모델 보조 점수와 사람 점수가 충돌하면 개별 trace, 계산과 출처를 다시 검토한다.

자세한 설계와 수용 기준은 [행동 평가와 회귀 테스트](../references/evaluation.md)를 참조한다.
