# Changelog

## Unreleased

- 행동 비교에 `feynman-implicit` 조건을 추가해 스킬 설치 상태에서 원문만 전달했을 때의 암묵 발동과 행동 변화를 별도로 측정함.
- 평가 실행 순서를 seed 기반으로 무작위화하고, 스킬 패키지 SHA-256·저장소 커밋·Codex CLI·Python·플랫폼 버전을 실행 메타데이터에 기록함.
- 후보 과제를 `codex exec -`의 표준 입력으로 전달해 긴 프롬프트의 명령줄 길이와 쉘 인용 의존성을 줄임.
- JSONL trace의 완료 이벤트만 집계하도록 보정하고 스킬 파일 참조 이벤트를 보조 신호로 기록함.
- Codex 인증 없이 A/B/C/D 작업 행렬을 검사할 수 있는 `run_evals.py --dry-run`을 추가함.
- 조건명을 숨긴 사람 검토용 `prepare_blind_review.py`와 0/1/2/NA 채점 패킷을 추가함.
- validator가 모든 로컬 Markdown 링크, 중첩 metadata 버전, 평가 도메인·발동 모드, JSON Schema, Python 문법과 A/B/C/D dry-run 조립을 검사하도록 강화함.
- GitHub Actions에서 정적 검증과 평가 행렬 조립을 자동으로 회귀 검사함.

## 0.4.0

- 스킬의 목적을 “파인만과 동일한 내부 사고”가 아니라 외부에서 검증 가능한 연구 행동의 재현으로 명확히 제한함.
- 수행 알고리즘을 `이름 제거 → 독립 모델 → 현실 점검 → 판별 공격 → 실패 후 수정`의 파인만 핵심 루프로 재구성함.
- 보고서 형식보다 실제 계산·검색·실행·자료 비교를 우선하는 `work-before-report` 규칙을 추가함.
- 장난감 사례, 규모 추정, 경계·극한, 대안 표현과 쉬움-정밀함 교차 검사를 강화함.
- 주장별 검사에 `N/A`를 추가해 관련 없는 근사·수식·템플릿을 강제하지 않도록 함.
- 자기비판에 형식 준수 가장, 실행하지 않은 검사 주장, 비판 문단과 결론 분리 등을 탐지하는 화물 숭배 감사를 추가함.
- 평가 루브릭을 `0/1/2/N/A`로 개편하고 일반 품질과 파인만 특이 지표를 분리함.
- baseline / generic / feynman A/B/C 비교용 held-out reasoning cases, JSON Schema, `codex exec --json` 실행기와 구조화 채점기를 추가함.
- `skills/feynman-thinking/`을 단일 정본으로 정하고 루트의 중복 스킬 사본을 제거함.

## 0.3.0

- Open Agent Skills 형식에 맞춰 폴더명과 frontmatter `name`을 일치시킴.
- 임의의 최상위 `version`, `language` 필드를 `metadata` 아래로 이동함.
- 긴 근거와 절차를 `references/`로 분리해 누진적 로딩 구조를 적용함.
- 재사용 작업 문서를 `assets/`로 분리함.
- `agents/openai.yaml`을 추가해 표시 이름과 암묵 호출 정책을 명시함.
- 발동 평가 사례와 로컬 검증 스크립트를 추가함.
- 반증 편향을 주장 유형별 검증으로 교정하고, 사회·정책 도메인 가드레일을 강화함.
