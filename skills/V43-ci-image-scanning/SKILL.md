# V43 — ci-image-scanning

> **Owner**: `hooks/validators/ci_image_scanning.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse)
> **File patterns**: `.github/workflows/*.yml`, `.github/workflows/*.yaml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V43-NO-IMAGE-SCAN` | error | A workflow job performs `docker build` or uses `docker/build-push-action`, but no downstream step or job scans the built image with a known scanner (Trivy, Grype, Snyk Container, Anchore, Docker Scout). |

## Why this verifier exists

Container image security is a critical supply-chain attack surface. Three failure modes compound:

1. **Build without scan.** A `docker build` in CI/CD completes without ever verifying the resulting image for vulnerabilities. Malicious base-layer CVEs, transitive dependency exploits, or misconfigured binaries ship silently.
2. **No gating on scan results.** A scan runs but the step doesn't fail on `CRITICAL` severity. The workflow proceeds to registry push or deployment, storing/deploying an unvetted image.
3. **Compliance gap (medical/finance).** Audit and regulatory frameworks require documented evidence that artifacts are scanned before deployment. A workflow without scanning leaves no audit trail.

V43 enforces that every `docker build` step is followed by an image scanner, failing the job on CRITICAL findings, with audit-trail visibility.

Evidence: grep over `.github/workflows/` for `trivy|grype|snyk|scout|anchore` returned 0 hits. `e2e.yml` uses `docker compose` to bring up infrastructure but does not scan any built image (verified at `/Users/taehyun/github/ai-project-template/.github/workflows/`).

## Design rationale

- **Scan is mandatory after every build.** A job with `docker build` but no scanner is incomplete. The job must either:
  - Run the scanner in the same job after the build step, or
  - Trigger a downstream job via `needs: build-job` that performs the scan.
- **Scanner detection is by step action or command.** V43 looks for steps containing:
  - `aquasecurity/trivy-action` (GitHub Action)
  - `anchore/scan-action` (Anchore's Action)
  - `snyk/actions/docker` (Snyk's Action)
  - `docker/scout-action` (Docker Scout's Action)
  - Or a bare step with `grype` in the shell command.
- **Only HTTP-service Dockerfiles require scanning.** Workers and non-containerized artifacts don't necessarily need image scanning; however, if the workflow explicitly builds and pushes a container, it should scan.
- **CRITICAL is the minimum gate.** Fail on `CRITICAL`; `HIGH` is a policy choice. V43 enforces `CRITICAL` as baseline; stricter thresholds are project config.
- **Scan can be same job or downstream.** A build job that outputs an image URI can be consumed by a downstream scan job (`needs: ["build"]`), reducing the single job's complexity.

## How it checks (implementation plan)

Lives in `hooks/validators/ci_image_scanning.py`.

### Top-level

```python
def validate_file(self, ctx, file_path: Path):
    if not file_path.name.endswith((".yml", ".yaml")):
        return
    if ".github/workflows" not in str(file_path):
        return
    
    findings = []
    findings.extend(self._check_image_scans(file_path))
    return findings
```

### `_check_image_scans(file_path)` — V43-NO-IMAGE-SCAN

```python
def _check_image_scans(self, file_path):
    try:
        data = yaml.safe_load(file_path.read_text())
    except Exception:
        return
    
    if not isinstance(data, dict) or "jobs" not in data:
        return
    
    jobs = data.get("jobs", {})
    if not isinstance(jobs, dict):
        return
    
    findings = []
    
    # For each job, check if it builds images
    for job_name, job_config in jobs.items():
        if not isinstance(job_config, dict):
            continue
        
        # Detect build step
        if not self._has_docker_build(job_config):
            continue
        
        # Job has docker build — check for scanner
        if not self._has_scanner(job_config, jobs, job_name):
            findings.append(Finding(
                rule="V43-NO-IMAGE-SCAN",
                file=str(file_path),
                message=f"Job '{job_name}' builds Docker image but no scanner detected",
                line=self._line_of_job(file_path, job_name)
            ))
    
    return findings
```

### `_has_docker_build(job_config)` — detect build step

```python
def _has_docker_build(self, job_config):
    """Check if job contains 'docker build' or 'docker/build-push-action'."""
    steps = job_config.get("steps", [])
    if not isinstance(steps, list):
        return False
    
    for step in steps:
        if not isinstance(step, dict):
            continue
        
        uses = step.get("uses", "")
        run = step.get("run", "")
        
        # Check for explicit action
        if "docker/build-push-action" in uses:
            return True
        
        # Check for shell command
        if "docker build" in run or "docker buildx build" in run:
            return True
    
    return False
```

### `_has_scanner(job_config, all_jobs, job_name)` — detect scanner step or dependency

```python
SCANNER_ACTIONS = [
    "aquasecurity/trivy-action",
    "anchore/scan-action",
    "snyk/actions/docker",
    "docker/scout-action"
]

SCANNER_COMMANDS = ["trivy", "grype", "snyk", "scout", "anchore"]

def _has_scanner(self, job_config, all_jobs, job_name):
    """Check if job has scanner step or if a downstream job scans."""
    
    # Check within same job
    steps = job_config.get("steps", [])
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            
            uses = step.get("uses", "").lower()
            run = step.get("run", "").lower()
            
            for action in self.SCANNER_ACTIONS:
                if action in uses:
                    return True
            
            for cmd in self.SCANNER_COMMANDS:
                if cmd in run:
                    return True
    
    # Check if downstream job depends and scans
    for other_name, other_job in all_jobs.items():
        if not isinstance(other_job, dict):
            continue
        
        needs = other_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        
        if job_name in needs and self._has_scanner(other_job, all_jobs, other_name):
            return True
    
    return False
```

### Could be more effective

- **Scan severity gating.** Currently V43 only checks presence. A stricter rule could verify the step includes `severity: CRITICAL` or equivalent fail-threshold parameter.
- **Image artifact tracking.** If build step outputs `image: myapp:${{ github.sha }}`, scanner should reference that exact URI. Currently V43 doesn't verify the image URI matches.
- **Multi-arch builds.** `docker buildx build --platform linux/amd64,linux/arm64` produces multiple images; scanner must run for each or the summary image. Complex edge case not yet handled.
- **Build cache validation.** A `--cache-from` that pulls from untrusted registry could reuse malicious layers; V43 doesn't check cache sources.
- **Scan report storage.** SARIF / JSON reports should be uploaded to GitHub Security tab. V43 doesn't enforce report upload.

## References

- [Trivy GitHub Action](https://github.com/aquasecurity/trivy-action) — Aqua Security, *continuously developed since 2020-09*, retrieved 2026-04-30. The most widely used open-source container scanner in CI/CD.
- [Trivy — Container scanning](https://aquasecurity.github.io/trivy/latest/docs/container/scanning/) — Aqua Security, *continuously updated*, retrieved 2026-04-30. How to run Trivy and interpret CRITICAL/HIGH/MEDIUM severities.
- [SLSA framework — Level 2 (Provenance)](https://slsa.dev/spec/v1.0/levels) — SLSA maintainers, *published 2023-04*, retrieved 2026-04-30. Supply-chain integrity standard; artifact scanning is a required control.
- [CIS Docker Benchmark v1.6 — Section 4.8: Ensure images are scanned and rebuilt frequently](https://www.cisecurity.org/benchmark/docker) — CIS, *published 2023-09*, retrieved 2026-04-30. Industry best practice for container image security.
- [Docker Scout — Image scanning](https://docs.docker.com/scout/) — Docker, *continuously updated*, retrieved 2026-04-30. Native Docker alternative to Trivy/Grype.

## Examples

### ✓ Pass

```yaml
# .github/workflows/ci.yml — Trivy scan in same job
name: CI

jobs:
  build-and-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Build image
        run: docker build -t myapp:${{ github.sha }} .
      
      - name: Scan with Trivy
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: myapp:${{ github.sha }}
          format: 'sarif'
          output: 'trivy-results.sarif'
          severity: 'CRITICAL'
      
      - name: Upload Trivy results
        uses: github/codeql-action/upload-sarif@v2
        with:
          sarif_file: 'trivy-results.sarif'
```

### ✓ Pass (downstream job)

```yaml
# .github/workflows/ci.yml — build + scan in separate jobs
name: CI

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          push: true
          tags: gcr.io/myproject/app:${{ github.sha }}
  
  scan:
    needs: [build]
    runs-on: ubuntu-latest
    steps:
      - name: Scan with Grype
        run: |
          grype gcr.io/myproject/app:${{ github.sha }} \
            --fail-on critical
```

### ✗ Fail

```yaml
# .github/workflows/ci.yml — docker build with no scanner
name: CI

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Build image
        run: docker build -t myapp:latest .
      
      - name: Push to registry
        run: docker push myapp:latest
      # → V43-NO-IMAGE-SCAN
      #   (docker build present, no scanner step)
```

```yaml
# .github/workflows/ci.yml — docker/build-push-action with no scan
name: CI

jobs:
  build-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          push: true
          tags: myregistry/myapp:latest
      # → V43-NO-IMAGE-SCAN
      #   (docker/build-push-action used, no downstream scan job
      #    and no scanner step in same job)
```
