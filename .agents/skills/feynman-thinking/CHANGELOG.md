# Changelog

## 0.3.0

- Open Agent Skills 형식에 맞춰 폴더명과 frontmatter `name`을 일치시킴.
- 임의의 최상위 `version`, `language` 필드를 `metadata` 아래로 이동함.
- 긴 근거와 절차를 `references/`로 분리해 누진적 로딩 구조를 적용함.
- 재사용 작업 문서를 `assets/`로 분리함.
- `agents/openai.yaml`을 추가해 표시 이름과 암묵 호출 정책을 명시함.
- 발동 평가 사례와 로컬 검증 스크립트를 추가함.
- 반증 편향을 주장 유형별 검증으로 교정하고, 사회·정책 도메인 가드레일을 강화함.
