---
name: write-business-function
description: 비즈니스 로직 함수를 작성/수정할 때 적용. Python / Go / TypeScript 모두에 대해 input validation → context validation → throw 순서 + 언어별 idiomatic docstring/comment 강제. services/, usecase/, handlers/, internal/business/ 등의 함수에 자동 적용.
---

# Business 함수 작성 — 3-Phase 규율

이 skill 은 **함수를 쓰기 전/쓰는 동안** 적용합니다. verifier (post-hoc 검사) 가 아니라 **write-time 가이드**.

## 1. 언제 적용?

✅ **적용 대상**: 비즈니스 로직 함수
- `services/`, `usecase/`, `handlers/`, `internal/business/`, `internal/services/` 안의 함수
- DB 쓰기, 파일 I/O, 네트워크 호출, 큐 publish 같은 사이드 이펙트가 있음
- HTTP handler, queue consumer, cron job 에서 호출됨
- 입력에서 결과를 계산하는 비-trivial transformation

❌ **건너뛰기**:
- 순수 utility (math, string, format)
- 테스트 헬퍼 / fixture
- 단순 getter (`get_user_id` → `return self._user_id`)
- 설정 로더 (validation 보통 라이브러리에 위임)

## 2. 3-Phase 함수 구조

```
함수 진입
   │
   ├─ Phase 1: Input Validation
   │     • 파라미터 자체의 shape / 값 / nullness
   │     • 외부 호출 없음 (싸게 fail)
   │     • 위반 시 raise/throw/return-error
   │
   ├─ Phase 2: Context Validation
   │     • 환경/시스템 상태 (디렉토리 존재, 권한, env var)
   │     • 외부 호출 가능 (filesystem, DB ping)
   │     • 위반 시 raise/throw/return-error
   │
   └─ Phase 3: Business Logic
         • 진짜 일을 하는 부분
         • 여기까지 도달하면 input + context 가 모두 valid 임이 보장됨
```

### 왜 이 순서인가

1. **Input 먼저** — 파라미터 검사는 사이드 이펙트 0. 잘못된 입력으로 I/O 시작하기 전에 거름.
2. **Context 다음** — Input 이 valid 일 때만 의미 있음.
3. **Throw, don't silent-return** — 무성한 `return None` / `return null` 은 디버깅 시간 #1 원인. 호출자가 모르는 채로 진행되면 데이터 무결성 깨짐.

## 3. 언어별 idiom

### Python

```python
def save_dicom_series(series: DicomSeries, target: Path) -> None:
    """Persist a DICOM series to disk.

    Each frame is written as ``<target>/<series.uid>/frame-NNN.dcm``.

    Args:
        series: The series to write. Must contain at least one frame.
        target: Output directory. Must exist; will not be created.

    Raises:
        ValueError: If series is empty or has zero frames.
        FileNotFoundError: If target dir doesn't exist.
        PermissionError: If target dir isn't writable.
    """
    # Phase 1: Input validation — parameter-level only
    if series is None:
        raise ValueError("series is None")
    if not series.frames:
        raise ValueError("series.frames is empty")

    # Phase 2: Context validation — environment / system state
    if not target.exists():
        raise FileNotFoundError(f"target dir not found: {target}")
    if not os.access(target, os.W_OK):
        raise PermissionError(f"target not writable: {target}")

    # Phase 3: Business logic — guaranteed valid inputs + context
    series_dir = target / series.uid
    series_dir.mkdir(exist_ok=True)
    for idx, frame in enumerate(series.frames):
        frame_path = series_dir / f"frame-{idx:03d}.dcm"
        frame.save(frame_path)
```

**Python 규칙**:
- Docstring: Google-style (`Args:` / `Returns:` / `Raises:` 섹션). pydocstyle / ruff `D101-D107` 가 enforce.
- `raise ValueError`, `raise TypeError`, `raise FileNotFoundError` 등 표준 예외 우선.
- 커스텀 예외는 도메인이 클 때만 (`class DicomSeriesError(Exception): pass`).
- Type hints 필수 (mypy / pyright 가 잡아줌).

### Go

```go
// SaveDicomSeries writes a DICOM series to disk under target/<series.UID>.
// Each frame becomes target/<UID>/frame-NNN.dcm.
//
// Returns ErrInvalidArgument if series is nil or has zero frames.
// Returns os.ErrNotExist (wrapped) if target doesn't exist.
// Returns os.ErrPermission (wrapped) if target isn't writable.
func SaveDicomSeries(ctx context.Context, series *DicomSeries, target string) error {
    // Phase 1: Input validation
    if series == nil {
        return fmt.Errorf("save_dicom_series: %w: series is nil", ErrInvalidArgument)
    }
    if len(series.Frames) == 0 {
        return fmt.Errorf("save_dicom_series: %w: series has zero frames", ErrInvalidArgument)
    }

    // Phase 2: Context validation
    info, err := os.Stat(target)
    if err != nil {
        return fmt.Errorf("save_dicom_series: stat target %q: %w", target, err)
    }
    if !info.IsDir() {
        return fmt.Errorf("save_dicom_series: target %q is not a directory", target)
    }

    // Phase 3: Business logic
    seriesDir := filepath.Join(target, series.UID)
    if err := os.MkdirAll(seriesDir, 0o755); err != nil {
        return fmt.Errorf("save_dicom_series: mkdir %q: %w", seriesDir, err)
    }
    for i, frame := range series.Frames {
        framePath := filepath.Join(seriesDir, fmt.Sprintf("frame-%03d.dcm", i))
        if err := frame.Save(framePath); err != nil {
            return fmt.Errorf("save_dicom_series: save frame %d: %w", i, err)
        }
    }
    return nil
}
```

**Go 규칙**:
- Comment: 함수 이름으로 시작 (`// SaveDicomSeries ...`). godoc 컨벤션.
- 가능한 모든 error case 를 godoc 에 명시 ("Returns X if ...").
- `panic` 은 진짜 unrecoverable 한 invariant 위반 (nil func 호출 등) 에만. 일반 비즈니스 실패는 `error` 반환.
- Error wrapping: `fmt.Errorf("...: %w", err)` 로 `errors.Is`/`errors.As` 가 작동.
- Sentinel error: `var ErrInvalidArgument = errors.New("invalid argument")` 패키지 레벨 정의.
- Error 메시지: 함수명 prefix + 무엇을/어디서 + wrapped error.

### TypeScript

```typescript
/**
 * Persist a DICOM series to disk under `target/<series.uid>`.
 * Each frame becomes `target/<uid>/frame-NNN.dcm`.
 *
 * @param series - Must contain at least one frame.
 * @param target - Output directory. Must exist; will not be created.
 *
 * @throws Error with code `INVALID_ARGUMENT` if series is empty.
 * @throws Error with code `NOT_FOUND` if target dir doesn't exist.
 * @throws Error with code `PERMISSION_DENIED` if target isn't writable.
 */
export async function saveDicomSeries(
    series: DicomSeries,
    target: string,
): Promise<void> {
    // Phase 1: Input validation
    if (!series) {
        throw new Error('INVALID_ARGUMENT: series is null/undefined');
    }
    if (!series.frames || series.frames.length === 0) {
        throw new Error('INVALID_ARGUMENT: series has zero frames');
    }

    // Phase 2: Context validation
    let stats: Stats;
    try {
        stats = await fs.stat(target);
    } catch {
        throw new Error(`NOT_FOUND: target ${target} doesn't exist`);
    }
    if (!stats.isDirectory()) {
        throw new Error(`INVALID_ARGUMENT: target ${target} is not a directory`);
    }

    // Phase 3: Business logic
    const seriesDir = path.join(target, series.uid);
    await fs.mkdir(seriesDir, { recursive: true });
    for (const [idx, frame] of series.frames.entries()) {
        const framePath = path.join(
            seriesDir,
            `frame-${String(idx).padStart(3, '0')}.dcm`,
        );
        await frame.save(framePath);
    }
}
```

**TypeScript 규칙**:
- JSDoc/TSDoc 블록 필수: `@param`, `@returns`, `@throws` (eslint-plugin-jsdoc 가 enforce).
- Error 코드 prefix 사용 (`INVALID_ARGUMENT:`, `NOT_FOUND:`) — 호출자가 message 파싱 또는 `error.code` 분기 가능.
- 커스텀 Error class 가 더 좋은 경우: 같은 도메인의 여러 케이스 분기 시.
  ```typescript
  class DicomSeriesError extends Error {
      constructor(public code: 'INVALID_ARGUMENT' | 'NOT_FOUND', message: string) {
          super(`${code}: ${message}`);
      }
  }
  ```
- async 함수는 `Promise<T>` 반환, 동기는 그대로 `T`.

## 4. 안티패턴 (절대 하지 말 것)

❌ **Silent return**:
```python
if not series.frames:
    return  # 호출자가 무엇이 잘못됐는지 알 수 없음
```

❌ **Logged-and-continued**:
```python
if not series.frames:
    logger.warning("empty series, skipping")
    return  # 로그만 남기고 무시 — 같은 문제
```

❌ **Mid-flow validation**:
```python
def save_dicom(series, target):
    series_dir = target / series.uid       # logic 시작
    series_dir.mkdir(exist_ok=True)         # logic
    for frame in series.frames:             # logic
        if not frame:                       # ← validation 너무 늦음
            raise ValueError("...")          # ← 이미 mkdir 됨
        frame.save(...)
```

❌ **Wrong order (context before input)**:
```python
def save_dicom(series, target):
    if not target.exists():           # context 먼저 ❌
        raise FileNotFoundError(...)
    if not series.frames:              # input check 가 더 늦게
        raise ValueError(...)
```

❌ **Returning error code instead of raising** (Python/TS — Go 는 다름):
```python
def save_dicom(series, target) -> dict:
    if not series.frames:
        return {"error": "empty series", "ok": False}  # ← 호출자가 검사 빼먹기 쉬움
```

> **Go 는 다름**: Go 의 `return err` 는 idiom. Anti-pattern 아님.
> Python/TS 에서 `return {ok: False}` 는 raise/throw 가 더 안전.

## 5. 함수 작성 전 체크리스트

함수를 commit 하기 전 다음 5 가지 통과:

- [ ] Docstring/comment 가 있고 brief + args + returns + raises 포함?
- [ ] Phase 1 (input validation) 이 함수 body 의 첫 executable 코드?
- [ ] Phase 2 (context validation) 가 Phase 1 다음에 옴?
- [ ] 모든 검증 실패가 raise/throw 또는 (Go) error 반환? — silent return 없음?
- [ ] Error 메시지에 파라미터 값 + 디버깅 컨텍스트 포함?

## 6. SDS / SRS / UT 트레이스 (의료 SW / regulated 만)

규제 환경 (IEC 62304, ISO 13485) 의 경우 함수 위에 SDS yaml frontmatter 추가:

```python
# /// SDS
# kind: SDS
# id: swiftmr-worker-0104
# revision: 2
# title: save_dicoms_to_directory SDS
# status: Draft
# traces:
#   from: [{ kind: SRS, id: swiftmr-0104 }]
#   to: [{ kind: DO, id: swiftmr-worker-0104 }]
#   verified_by: [{ kind: UT, id: swiftmr-worker-0104 }]
# ///
def save_dicom_series(series: DicomSeries, target: Path) -> None:
    """Persist a DICOM series to disk."""
    ...
```

`.verifiers/config.yaml` 의 `regulated_sw: true` 활성 시만 적용. 일반 프로젝트는 무시.

## 7. 더 깊이

- **Hoare logic precondition** — 이 패턴의 학술 기반. `{P} S {Q}` 에서 P 는 precondition (input + context).
- **Design by Contract** — Bertrand Meyer. precondition 깨지면 함수 책임이 아닌 호출자 책임.
- **Tiger Style** (TigerBeetle 데이터베이스 스타일 가이드, 2024) — "assertions everywhere, fail loud" 철학.
- **Google SRE: defensive programming** — 배포 환경에서 silent failure 가 가장 비싼 디버깅.

References:
- "Design by Contract, by Example" — Bertrand Meyer (Pearson, 2001)
- "TigerBeetle Tiger Style" — https://github.com/tigerbeetle/tigerbeetle/blob/main/docs/TIGER_STYLE.md
  (continuously updated, retrieved 2026-05-02)
- "Google SRE Book" — Ch. 11 (Being On-Call), Ch. 14 (Incident Management) on cost of silent failures
