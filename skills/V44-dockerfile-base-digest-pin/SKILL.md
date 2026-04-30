# V44 — dockerfile-base-digest-pin

> **Owner**: `hooks/validators/dockerfile_base_digest_pin.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse)
> **File patterns**: `**/Dockerfile*`, `**/*.Dockerfile`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V44-FROM-NO-DIGEST` | warning | A `FROM` line in a Dockerfile uses a tag (e.g., `FROM golang:1.25-bookworm`) without an `@sha256:<digest>` suffix. |

## Why this verifier exists

Docker registries can silently mutate image tags, introducing supply-chain drift:

1. **Tag mutation risk.** `docker build` with `FROM golang:1.25-bookworm` today pulls one binary. Next week, a new Go patch is released and the registry's `golang:1.25-bookworm` tag gets re-pushed. The next build pulls a different binary without explicit code review.
2. **Compliance audit gap.** Medical and fintech require audit trails showing exactly which artifact (SHA) was deployed. A tag-based FROM produces ambiguous audit logs ("it was 1.25-bookworm at some point").
3. **Cached builds hide the drift.** Docker layer cache makes the mutation invisible — a developer's cached image may differ from CI's fresh pull, causing "works on my machine" confusion.

V44 enforces digest-pinned base images (`FROM golang:1.25-bookworm@sha256:<64-hex-chars>`), making every build reproducible and auditable.

Evidence: `web/Dockerfile:75` — `FROM nginx:${NGINX_VERSION}-alpine AS prod` (no digest). `server/docker/server.Dockerfile:14,43,62` — `FROM golang:1.25-bookworm`, `FROM debian:bookworm-slim` (no digest). `server/airflow/Dockerfile:4` — same pattern. All have tag-only FROM lines (verified at `/Users/taehyun/github/ai-project-template/web/Dockerfile`, `server/docker/server.Dockerfile`, etc.).

## Design rationale

- **Distroless images get same treatment.** `gcr.io/distroless/static-debian12:nonroot` is pinned just like `alpine`. Distroless is a best practice, but unpinned is still a risk.
- **ARG substitution is handled carefully.** A Dockerfile with `ARG BASE_IMAGE=golang:1.25` and `FROM $BASE_IMAGE` cannot be pinned at build-time. V42 skips ARG-substituted lines that lack a default value in the ARG declaration.
- **Multi-stage FROM lines are all checked.** Every `FROM` line in the Dockerfile is verified, not just the final stage. Intermediate builder stages are equally important.
- **Renovate/Dependabot keep digests fresh.** Once pinned, the base images can be automated via Renovate (with `digest` automation enabled) or Dependabot. V42 doesn't require automation; it just enforces the pin structure.
- **`latest` tag is a special case.** `FROM golang:latest` is arguably worse than explicit version; V42 flags all unpinned tags equally.

## How it checks (implementation plan)

Lives in `hooks/validators/dockerfile_base_digest_pin.py`.

### Top-level

```python
def validate_file(self, ctx, file_path: Path):
    if not self._is_dockerfile(file_path):
        return
    
    findings = []
    findings.extend(self._check_from_digests(file_path))
    return findings

def _is_dockerfile(self, path):
    """Check if file is a Dockerfile."""
    name = path.name
    return name == "Dockerfile" or name.endswith(".Dockerfile")
```

### `_check_from_digests(file_path)` — V44-FROM-NO-DIGEST

```python
FROM_PATTERN = re.compile(
    r"^\s*FROM\s+(?P<image>[^\s@]+)(?:@(?P<digest>sha256:[a-f0-9]{64}))?\s*(?:AS\s+\w+)?\s*(?:#|$)",
    re.IGNORECASE | re.MULTILINE
)

def _check_from_digests(self, file_path):
    """Check all FROM lines for digest pins."""
    text = file_path.read_text()
    
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        
        # Skip comments
        if stripped.startswith("#"):
            continue
        
        # Match FROM line
        m = self.FROM_PATTERN.match(stripped)
        if not m:
            continue
        
        image = m.group("image")
        digest = m.group("digest")
        
        # If ARG-substituted, check if it's a well-known ARG
        if "$" in image:
            if not self._is_known_arg(image, text):
                # Skip — ARG-substituted, allow it
                continue
        
        # Check digest
        if not digest:
            yield Finding(
                rule="V44-FROM-NO-DIGEST",
                file=str(file_path),
                line=line_no,
                message=f"FROM {image} lacks @sha256:<digest> pin"
            )

def _is_known_arg(self, image, dockerfile_text):
    """Check if image variable has a pinned default in ARG."""
    # Extract variable name (e.g., BASE_IMAGE from ${BASE_IMAGE})
    var_match = re.search(r"\$\{?(\w+)\}?", image)
    if not var_match:
        return False
    
    var_name = var_match.group(1)
    
    # Look for ARG var_name=<image with digest>
    arg_pattern = re.compile(
        rf"^\s*ARG\s+{var_name}=(?P<default>[^\s]+)(?:@sha256:[a-f0-9]{{64}})?",
        re.MULTILINE
    )
    
    m = arg_pattern.search(dockerfile_text)
    if m:
        default = m.group("default")
        # If default has digest, allow it
        if "@sha256:" in default:
            return True
    
    return False
```

### Could be more effective

- **Warn on `latest` tag specifically.** `FROM golang:latest@sha256:...` is pinned but semantically dangerous (the digest will change when latest updates). Could emit a separate lower-severity hint.
- **Validate digest correctness.** V44 just checks for 64 hex characters. Could verify the digest actually matches the image tag by inspecting the registry (requires auth; scope creep).
- **Detect multi-line FROM.** Some Dockerfiles split `FROM golang:1.25 as builder \` across multiple lines; the current regex may miss it.
- **Private registry digest format.** Some registries use different digest algorithms (`sha512`, etc.). V44 currently enforces `sha256` only; could be parameterized.

## References

- [Docker — Use trusted base images](https://docs.docker.com/build/building/best-practices/#use-trusted-base-images) — Docker, *continuously updated*, retrieved 2026-04-30. Recommendation to pin base images by digest for reproducibility.
- [CIS Docker Benchmark v1.6 — 4.8: Ensure images are scanned and rebuilt frequently](https://www.cisecurity.org/benchmark/docker) — CIS, *published 2023-09*, retrieved 2026-04-30. Industry standard for immutable artifact chains.
- [Docker — Image reference format](https://docs.docker.com/engine/reference/commandline/image/) — Docker, *continuously updated*, retrieved 2026-04-30. Syntax for `image:tag@sha256:<digest>` form.
- [Renovate — Docker digest automation](https://docs.renovatebot.com/modules/manager/docker/#digest-pinning) — Renovate, *continuously updated*, retrieved 2026-04-30. How to automatically update pinned digests.
- [SLSA framework — Provenance](https://slsa.dev/spec/v1.0/provenance) — SLSA, *published 2023-04*, retrieved 2026-04-30. Supply-chain integrity requires artifact immutability; digest pinning is foundational.

## Examples

### ✓ Pass

```dockerfile
# Dockerfile — all FROM lines pinned by digest
FROM golang:1.25-bookworm@sha256:abc123def456...
RUN go build .

FROM debian:bookworm-slim@sha256:xyz789uvw...
COPY --from=0 /app/binary /usr/local/bin/
```

```dockerfile
# ARG with pinned default
ARG BASE_IMAGE=alpine:3.20@sha256:abc...
FROM $BASE_IMAGE
RUN apk add --no-cache curl
```

```dockerfile
# Distroless pinned
FROM gcr.io/distroless/static-debian12:nonroot@sha256:abc123...
COPY app /app
ENTRYPOINT ["/app"]
```

### ✗ Fail

```dockerfile
# Dockerfile — unpinned tag
FROM golang:1.25-bookworm
RUN go build .
# → V44-FROM-NO-DIGEST (line 1: FROM golang:1.25-bookworm lacks @sha256:<digest> pin)
```

```dockerfile
# Multiple unpinned stages
FROM nginx:latest
# → V44-FROM-NO-DIGEST

FROM alpine
# → V44-FROM-NO-DIGEST
```

```dockerfile
# Distroless unpinned
FROM gcr.io/distroless/static-debian12:nonroot
COPY app /app
# → V44-FROM-NO-DIGEST
```

```dockerfile
# ARG without pinned default
ARG BASE_IMAGE=golang:1.25-bookworm
FROM $BASE_IMAGE
# → V44-FROM-NO-DIGEST (ARG default lacks digest)
```
