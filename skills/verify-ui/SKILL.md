---
name: verify-ui
description: UI 구현이 디자인 스펙과 일치하는지 Chrome DevTools + Pencil MCP로 검증. 레이아웃, 색상, 타이포, 반응형, 접근성 확인.
---

## UI 검증 모드가 활성화되었습니다.

이제 UI 컴포넌트를 수정하면 다음이 자동으로 수행됩니다:
1. Chrome DevTools MCP로 실제 렌더링 결과 스크린샷
2. 디자인 스펙(Pencil/Figma)과 시각적 비교
3. DOM 구조 및 CSS 속성 검증
4. 차이점 발견 시 수정 지시

### 사전 조건
- dev 서버가 실행 중이어야 합니다 (localhost:7777 또는 해당 포트)
- Chrome DevTools MCP가 연결되어 있어야 합니다

### 사용법
- 디자인 파일(.pen)이 있으면 자동으로 비교합니다
- Figma URL을 제공하면 해당 디자인과 비교합니다
- 스크린샷을 직접 제공할 수도 있습니다

### 검증 항목

#### 레이아웃
- 요소 위치, 크기, 간격 (design spec 대비)
- Flex/Grid 레이아웃 정확성
- 반응형: 모바일(375)/태블릿(768)/데스크탑(1920) 레이아웃

#### 색상
- `theme.palette` 사용 여부 (하드코딩 색상 금지)
- 디자인 색상과 실제 렌더링 색상 일치

#### 타이포그래피
- font-size, font-weight, line-height
- 텍스트 overflow 처리 (ellipsis, wrap)

#### 접근성
- aria-label, role 속성 존재
- 키보드 탐색 (tab 순서)
- 충분한 색상 대비 (WCAG AA)

### 전용 에이전트 위임

더 포괄적인 UI 검증이 필요하면 `ui-verifier` 에이전트에 위임하세요:
```
Agent tool → subagent_type: "general-purpose"
name: "ui-verifier"
```

이 에이전트는 3가지 뷰포트에서 스크린샷을 찍고, 디자인 스펙과 비교하여 상세 리포트를 생성합니다.
