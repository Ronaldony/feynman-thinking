# feynman-thinking

리처드 파인만의 말투가 아니라 연구 실천에서 도출한 재표현, 모델링, 근사 관리, 판별 검증, 자기기만 방지 절차를 일반 작업에 적용하는 Agent Skill이다.

## 설치

이 폴더 전체를 다음 경로에 둔다.

```text
<repository-root>/.agents/skills/feynman-thinking/
```

사용자 전역 설치는 다음 경로를 사용할 수 있다.

```text
$HOME/.agents/skills/feynman-thinking/
```

Codex에서 `$feynman-thinking`으로 명시 호출하거나, 설명과 맞는 복잡한 검증 작업에서 암묵적으로 호출한다.

## 검증

```bash
python .agents/skills/feynman-thinking/scripts/validate_skill.py
```

공식 `skills-ref` 도구가 설치되어 있으면 추가로 다음을 실행한다.

```bash
skills-ref validate .agents/skills/feynman-thinking
```

## 구성

- `SKILL.md`: 발동 조건과 핵심 수행 규칙
- `references/`: 상세 절차, 검증 행렬, 논문 근거, 자기비판, 평가 기준
- `assets/`: 문제 계약, 모델 카드, 근사 원장, 검증 기록, 최종 보고서 양식
- `evals/`: 발동 회귀 테스트
- `scripts/validate_skill.py`: 표준 라이브러리 기반 로컬 구조 검사
- `agents/openai.yaml`: 표시 이름과 암묵 호출 정책
