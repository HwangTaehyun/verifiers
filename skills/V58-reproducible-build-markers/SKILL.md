# V58 — reproducible-build-markers

> **Owner**: `hooks/validators/reproducible_build_markers.py`
> **Tier**: 2 (PostToolUse) + 3 (Stop)
> **File patterns**: `**/Dockerfile*`, `**/*.Dockerfile`, `.github/workflows/*.yml`, `.github/workflows/*.yaml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V58-NO-SOURCE-DATE-EPOCH` | warning | A production Dockerfile's final stage lacks `ARG SOURCE_DATE_EPOCH`, `ENV SOURCE_DATE_EPOCH=`, or `--build-arg SOURCE_DATE_EPOCH`, and no CI workflow compensates by passing it via `build-args:` or exporting the env var. |

## Why this verifier exists

Docker image layer hashes depend on timestamps embedded by the build toolchain. Without pinning `SOURCE_DATE_EPOCH`, two builds from bit-for-bit identical source code can produce different image digests. This breaks:

1. **Attestation and SBOM verification.** Supply-chain frameworks (SLSA, in-toto) require that a rebuild of the same commit produces the same artifact hash. Timestamp drift silently invalidates provenances.
2. **Registry caching efficiency.** Layer hashes that change on every build defeat content-addressable caching in registries and CI caches, increasing bandwidth and build time even when source is unchanged.
3. **Incident forensics.** When digests drift across identical builds, it becomes impossible to verify post-incident whether a deployed image truly matched the audited source commit.
4. **Multi-arch consistency.** Cross-platform builds (`--platform linux/amd64,linux/arm64`) that run on different builders at different clock times produce divergent digests unless `SOURCE_DATE_EPOCH` is fixed.

`SOURCE_DATE_EPOCH` is the ecosystem-wide standard accepted by GNU Make, CMake, Go, Rust, Python packaging, and many other tools — declaring it in the Dockerfile or passing it via build-args is the minimal intervention that unlocks full reproducibility.

## Design rationale

- **Final stage only.** Reproducibility affects the shipped image, which is the last `FROM` stage. Intermediate builder stages that do not appear in the final image are irrelevant to the attestation hash.
- **Workflow as an alternative path.** Many teams prefer not to embed `SOURCE_DATE_EPOCH` in the Dockerfile itself and instead pass it at CI build time via `build-args:` in `docker/build-push-action`. V58 accepts either form — if a workflow passes the variable, the Dockerfile is considered compliant.
- **Dev Dockerfiles are exempt.** Files whose name contains `dev` (e.g. `Dockerfile.dev`, `dev.Dockerfile`) or whose final stage has an AS alias containing `dev` (with no `prod*` stage) are not production artifacts; reproducibility is not a concern for local development images.
- **Regex-only workflow parsing.** Full YAML parsing is deliberately avoided to keep the validator lightweight and resilient to YAML syntax variations. A coarse step-block split combined with keyword search is sufficient to detect `SOURCE_DATE_EPOCH` in `build-args:` blocks.
- **Warning, not error.** Reproducibility is a supply-chain best practice, not an immediate security incident. V58 emits `warning` so the team is informed without blocking unrelated changes.

## How it checks

Lives in `hooks/validators/reproducible_build_markers.py`.

### Entry points

```python
def validate_file(self, ctx, file_path: str) -> list[Finding]:
    # Tier 2: delegates to _check(ctx) — project-wide context is needed
    # to determine whether a CI workflow compensates for the Dockerfile.
    return self._check(ctx)

def validate_project(self, ctx) -> list[Finding]:
    # Tier 3: same — full project sweep.
    return self._check(ctx)
```

Both tiers delegate to `_check` because the pass/fail decision for a single Dockerfile depends on whether *any* workflow in `.github/workflows/` passes `SOURCE_DATE_EPOCH`. Scanning only the edited file would miss the cross-file relationship.

### `_check(ctx)` — top-level flow

```
1. Glob all Dockerfile* / *.Dockerfile under project_root.
2. If none found → return [] (not applicable).
3. Filter to production Dockerfiles (_is_dev_dockerfile → False).
4. If none remain → return [].
5. Check workflows: _workflow_satisfies_sde(root) → True if any workflow
   passes SOURCE_DATE_EPOCH near a docker build step.
6. For each prod Dockerfile:
   - If workflow satisfies OR _dockerfile_has_sde_in_final_stage → skip.
   - Otherwise → emit V58-NO-SOURCE-DATE-EPOCH warning.
```

### `_dockerfile_has_sde_in_final_stage(path)`

Scans lines from the last `FROM` onward, matching:
- `ARG SOURCE_DATE_EPOCH`
- `ENV SOURCE_DATE_EPOCH=`
- `--build-arg SOURCE_DATE_EPOCH`

### `_workflow_passes_sde(path)`

1. Fast path: if `"SOURCE_DATE_EPOCH"` not in file text → return False.
2. If any line matches `SOURCE_DATE_EPOCH=` (export pattern) → return True.
3. Split text into rough step blocks (lines starting with `- `).
4. For each block containing `docker build` or `docker/build-push-action`,
   check if `SOURCE_DATE_EPOCH` also appears → return True.

### Dev exemption heuristic (`_is_dev_dockerfile`)

- Filename contains `"dev"` (case-insensitive) → exempt.
- Final `FROM` stage alias contains `"dev"` AND no other stage alias contains `"prod"` → exempt.

## Could be more effective

- **Warn when SOURCE_DATE_EPOCH is declared but not propagated.** `ARG SOURCE_DATE_EPOCH` without a subsequent `ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}` means Go/Rust tooling in the container won't see it. Could add a second rule `V58-SDE-NOT-EXPORTED`.
- **Check that CI actually sets the variable to a meaningful value.** A workflow with `SOURCE_DATE_EPOCH=0` passes the check but defeats reproducibility (all builds would be identical to epoch-0, but wrong). Requires value-level analysis.
- **Extend to non-GitHub CI.** Only `.github/workflows/` is scanned. GitLab CI (`.gitlab-ci.yml`), CircleCI, and Buildkite configs could also carry the variable. Scope was kept minimal for this version.
- **Cross-validate with BuildKit `--build-arg` in Makefile/scripts.** Some projects invoke `docker build` directly in shell scripts or Makefiles. These are not currently detected.

## References

- [Reproducible Builds project](https://reproducible-builds.org/) — ecosystem initiative for bit-for-bit reproducible software builds — *continuously developed since 2015-01, retrieved 2026-04-30*
- [SOURCE_DATE_EPOCH specification](https://reproducible-builds.org/docs/source-date-epoch/) — canonical spec defining the `SOURCE_DATE_EPOCH` environment variable and its accepted values — *continuously updated, retrieved 2026-04-30*
- [Docker BuildKit documentation](https://docs.docker.com/build/buildkit/) — BuildKit build argument passing, `--build-arg`, and cache semantics — *continuously updated, retrieved 2026-04-30*
- [SLSA provenance spec v1.0](https://slsa.dev/spec/v1.0/provenance) — supply-chain integrity framework requiring artifact immutability; reproducible builds are foundational — *published 2023-04, retrieved 2026-04-30*
- [docker/build-push-action — build-args](https://github.com/docker/build-push-action#inputs) — GitHub Action input reference for passing build-time variables to BuildKit — *continuously developed since 2020-09, retrieved 2026-04-30*

## Examples

### Pass — ARG + ENV in final stage

```dockerfile
FROM golang:1.25-bookworm AS builder
RUN go build -o /app .

FROM debian:bookworm-slim AS prod
ARG SOURCE_DATE_EPOCH
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
COPY --from=builder /app /app
ENTRYPOINT ["/app"]
```

### Pass — workflow passes build-arg (Dockerfile needs no change)

```yaml
# .github/workflows/build.yml
- uses: docker/build-push-action@v5
  with:
    build-args: |
      SOURCE_DATE_EPOCH=${{ github.event.head_commit.timestamp }}
```

### Pass — single-stage with ENV

```dockerfile
FROM alpine:3.20
ENV SOURCE_DATE_EPOCH=0
RUN apk add --no-cache curl
```

### Fail — production Dockerfile, no marker, no workflow

```dockerfile
FROM golang:1.25-bookworm AS builder
RUN go build .

FROM debian:bookworm-slim AS prod
COPY --from=builder /app /app
# → V58-NO-SOURCE-DATE-EPOCH (warning)
# Fix: add `ARG SOURCE_DATE_EPOCH` + `ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}`
#      before the COPY line, or pass via CI build-args.
```

### Fail — intermediate stage has marker but final stage does not

```dockerfile
FROM golang:1.25-bookworm AS builder
ARG SOURCE_DATE_EPOCH       ← marker is here (intermediate) — not enough
RUN go build .

FROM debian:bookworm-slim AS prod
COPY --from=builder /app /app
# → V58-NO-SOURCE-DATE-EPOCH (warning)
# The FINAL stage (prod) must declare ARG SOURCE_DATE_EPOCH.
```

### Exempt — dev Dockerfile

```dockerfile
# Dockerfile.dev — filename contains "dev" → exempt, no warning
FROM node:20
RUN npm install
```

```dockerfile
# Final stage AS dev, no prod* stage → exempt
FROM node:20 AS deps
RUN npm ci

FROM node:20 AS dev
COPY --from=deps /app/node_modules .
```
