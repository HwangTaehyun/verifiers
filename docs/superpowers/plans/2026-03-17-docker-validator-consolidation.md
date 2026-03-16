# Docker Validator Consolidation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate V05 and V17 Docker validators into a single comprehensive validator with 17 validation rules covering Compose files, Dockerfiles, and production best practices.

**Architecture:** Extend existing V05 docker_compose.py to absorb all V17 functionality plus 3 new rules from DOCKER_BEST_PRACTICES.md. Delete V17 entirely and update tests/documentation to reflect unified approach.

**Tech Stack:** Python 3.11+, PyYAML, pytest, regex

---

## File Structure Analysis

**Files to modify:**
- `hooks/validators/docker_compose.py` - Expand from 5 to 17 validation rules
- `tests/test_docker_compose.py` - Absorb all test cases from V17 tests
- `/Users/taehyun/.claude/skills/verify-docker/SKILL.md` - Update description

**Files to delete:**
- `hooks/validators/docker_prod_deploy.py` - V17 validator (backup first)
- `tests/test_docker_prod_deploy.py` - V17 tests (merge content first)

**Files to create:**
- `docs/superpowers/plans/2026-03-17-docker-validator-consolidation.md` - This plan

## Chunk 1: Backup and Analysis

### Task 1: Create Safety Backups

**Files:**
- Create: `hooks/validators/docker_prod_deploy.py.backup`
- Create: `tests/test_docker_prod_deploy.py.backup`

- [ ] **Step 1: Backup V17 files**

```bash
cp hooks/validators/docker_prod_deploy.py hooks/validators/docker_prod_deploy.py.backup
cp tests/test_docker_prod_deploy.py tests/test_docker_prod_deploy.py.backup
```

- [ ] **Step 2: Verify backups exist**

```bash
ls -la hooks/validators/docker_prod_deploy.py.backup
ls -la tests/test_docker_prod_deploy.py.backup
```

Expected: Both backup files exist

- [ ] **Step 3: Commit backups**

```bash
git add hooks/validators/docker_prod_deploy.py.backup tests/test_docker_prod_deploy.py.backup
git commit -m "backup: create safety backups before V05/V17 consolidation"
```

### Task 2: Analyze V17 Code Structure

**Files:**
- Read: `hooks/validators/docker_prod_deploy.py`
- Read: `tests/test_docker_prod_deploy.py`

- [ ] **Step 1: Extract V17 validation methods**

Read V17 file and identify these methods to copy:
- `_check_dockerfile_multistage()`
- `_check_dockerfile_user()`
- `_check_dockerfile_expose()`
- `_check_dockerfile_copy_all()`
- `_check_prod_port_exposed()`
- `_check_prod_dev_mode()`
- `_check_prod_wildcard_cors()`
- `_check_dev_volume_mount()`
- `_check_dev_build_target()`

- [ ] **Step 2: Extract V17 test cases**

Read test file and identify test classes/methods to migrate:
- All test classes and methods that validate V17-* rules

- [ ] **Step 3: Document current V05 structure**

Read current V05 file and note existing methods:
- `_check_port_conflicts()`
- `_check_virtual_host_network()`
- `_check_network_references()`
- `_check_depends_on_healthcheck()`
- `_check_env_var_references()`

## Chunk 2: V05 Code Expansion

### Task 3: Add V17 Methods to V05

**Files:**
- Modify: `hooks/validators/docker_compose.py:1-50` (imports and class definition)
- Modify: `hooks/validators/docker_compose.py:70-500` (add new methods)

- [ ] **Step 1: Update class name and docstring**

```python
class DockerValidator(BaseValidator):
    """V05: 통합 Docker 검증 (Compose + Dockerfile + Production)

    Checks:
      Compose Files (5 rules):
        V05-PORT-CONFLICT: Two services mapping same host port
        V05-VHOST-NO-NETWORK: VIRTUAL_HOST set but not on nginx-proxy network
        V05-UNDEFINED-NETWORK: Service references network not defined in top-level
        V05-MISSING-HEALTHCHECK: depends_on condition: service_healthy but no healthcheck
        V05-MISSING-ENV-VAR: ${VAR} referenced without default and not in .env

      Dockerfile (4 rules):
        V05-DOCKERFILE-NO-USER: Production stage runs as root (missing USER directive)
        V05-DOCKERFILE-NO-EXPOSE: Missing EXPOSE directive in production stage
        V05-DOCKERFILE-COPY-ALL: COPY . . without .dockerignore may leak secrets
        V05-DOCKERFILE-NO-MULTISTAGE: Single-stage Dockerfile (no multi-stage build)

      Production Safety (3 rules):
        V05-PROD-PORT-EXPOSED: Production compose should not expose host ports
        V05-PROD-DEV-MODE: Dev mode enabled in production config
        V05-PROD-WILDCARD-CORS: CORS set to "*" in production

      Development Setup (2 rules):
        V05-DEV-NO-VOLUME-MOUNT: Dev override should mount source code for hot reload
        V05-DEV-NO-BUILD-TARGET: Dev override should set build.target to 'dev'

      Best Practices (3 rules):
        V05-BUILD-TARGET-MISSING: build.target doesn't exist in Dockerfile
        V05-BASE-IMAGE-LATEST: Using latest tag (not recommended)
        V05-MISSING-DOCKERIGNORE: .dockerignore missing with COPY . .
    """

    id = "V05-docker"
    name = "Docker Validator"
    file_patterns: list[str] = [
        "**/docker-compose*.yaml",
        "**/docker-compose*.yml",
        "**/Dockerfile*",
        "**/*.Dockerfile",
    ]
```

- [ ] **Step 2: Update validate method**

```python
def validate(
    self,
    ctx: ProjectContext,
    file_path: str | None = None,
    mode: str = "post_tool_use",
) -> ValidationResult:
    findings: list[Finding] = []

    # Find all compose files and dockerfiles
    compose_files = list(ctx.project_root.glob("**/docker-compose*.yaml"))
    compose_files.extend(ctx.project_root.glob("**/docker-compose*.yml"))
    compose_files = self._filter_excluded_files(compose_files)

    dockerfiles = list(ctx.project_root.glob("**/Dockerfile*"))
    dockerfiles.extend(ctx.project_root.glob("**/*.Dockerfile"))
    dockerfiles = self._filter_excluded_files(dockerfiles)

    # Validate compose files (existing V05 checks)
    for compose_file in compose_files:
        try:
            data = yaml.safe_load(compose_file.read_text()) or {}
        except (yaml.YAMLError, OSError):
            continue

        findings.extend(self._check_port_conflicts(data, compose_file))
        findings.extend(self._check_virtual_host_network(data, compose_file))
        findings.extend(self._check_network_references(data, compose_file))
        findings.extend(self._check_depends_on_healthcheck(data, compose_file))
        findings.extend(self._check_env_var_references(ctx, data, compose_file))

        # V17 production checks
        findings.extend(self._check_prod_port_exposed(data, compose_file))
        findings.extend(self._check_prod_dev_mode(data, compose_file))
        findings.extend(self._check_prod_wildcard_cors(data, compose_file))
        findings.extend(self._check_dev_volume_mount(data, compose_file))
        findings.extend(self._check_dev_build_target(data, compose_file))

    # Validate dockerfiles (V17 checks)
    for dockerfile in dockerfiles:
        findings.extend(self._check_dockerfile_multistage(dockerfile))
        findings.extend(self._check_dockerfile_user(dockerfile))
        findings.extend(self._check_dockerfile_expose(dockerfile))
        findings.extend(self._check_dockerfile_copy_all(ctx, dockerfile))

    # New cross-file validations
    findings.extend(self._check_build_target_exists(ctx, compose_files, dockerfiles))
    findings.extend(self._check_base_image_latest(dockerfiles))
    findings.extend(self._check_dockerignore_exists(ctx, dockerfiles))

    return ValidationResult(validator_id=self.id, findings=findings)
```

- [ ] **Step 3: Add helper method**

```python
def _filter_excluded_files(self, files: list[Path]) -> list[Path]:
    """Exclude vendor, node_modules, .git directories."""
    exclude = {"vendor", "node_modules", ".git", "__pycache__", ".venv"}
    return [f for f in files if not any(p in str(f) for p in exclude)]
```

- [ ] **Step 4: Commit basic structure**

```bash
git add hooks/validators/docker_compose.py
git commit -m "feat: expand V05 class definition for 17-rule consolidation"
```

### Task 4: Copy V17 Dockerfile Validation Methods

**Files:**
- Modify: `hooks/validators/docker_compose.py:400-600` (add dockerfile methods)

- [ ] **Step 1: Copy _check_dockerfile_multistage method**

```python
def _check_dockerfile_multistage(self, dockerfile: Path) -> list[Finding]:
    """Production Dockerfile should use multi-stage builds."""
    findings: list[Finding] = []
    try:
        content = dockerfile.read_text()
    except OSError:
        return findings

    from_count = len(re.findall(r"^FROM\s+", content, re.MULTILINE))
    if from_count < 2:
        findings.append(
            Finding(
                severity="warning",
                file=str(dockerfile),
                rule="V05-DOCKERFILE-NO-MULTISTAGE",
                message="Dockerfile has only one FROM stage (no multi-stage build)",
                fix=(
                    f"Use multi-stage build in {dockerfile.name}: "
                    f"dev stage (hot reload), builder stage (compile), "
                    f"prod stage (minimal runtime like alpine)"
                ),
            )
        )
    return findings
```

- [ ] **Step 2: Copy _check_dockerfile_user method**

```python
def _check_dockerfile_user(self, dockerfile: Path) -> list[Finding]:
    """Production stage should not run as root (must have USER directive)."""
    findings: list[Finding] = []
    try:
        content = dockerfile.read_text()
    except OSError:
        return findings

    # Find the last stage (after the last FROM)
    stages = re.split(r"^FROM\s+", content, flags=re.MULTILINE)
    if len(stages) < 2:
        return findings  # Single stage handled by multistage check

    last_stage = stages[-1]

    # Check if the last stage (assumed to be prod) has a USER directive
    if not re.search(r"^USER\s+", last_stage, re.MULTILINE):
        # Check if the stage name suggests it's a prod stage
        first_line = last_stage.strip().split("\n")[0]
        stage_name = ""
        as_match = re.search(r"\bAS\s+(\S+)", first_line, re.IGNORECASE)
        if as_match:
            stage_name = as_match.group(1).lower()

        # Only flag if the stage looks like a production stage
        if stage_name in ("prod", "production", "release", "final", "runtime", ""):
            findings.append(
                Finding(
                    severity="error",
                    file=str(dockerfile),
                    rule="V05-DOCKERFILE-NO-USER",
                    message=f"Production stage runs as root (missing USER directive)",
                    fix=(
                        f"Add a non-root user to the production stage in {dockerfile.name}: "
                        f"RUN addgroup -S app && adduser -S app -G app, then USER app"
                    ),
                )
            )
    return findings
```

- [ ] **Step 3: Copy _check_dockerfile_expose method**

```python
def _check_dockerfile_expose(self, dockerfile: Path) -> list[Finding]:
    """Dockerfile should have at least one EXPOSE directive."""
    findings: list[Finding] = []
    try:
        content = dockerfile.read_text()
    except OSError:
        return findings

    if not re.search(r"^EXPOSE\s+", content, re.MULTILINE):
        findings.append(
            Finding(
                severity="warning",
                file=str(dockerfile),
                rule="V05-DOCKERFILE-NO-EXPOSE",
                message="No EXPOSE directive found in Dockerfile",
                fix=f"Add EXPOSE <port> to {dockerfile.name} to document the container port",
            )
        )
    return findings
```

- [ ] **Step 4: Copy _check_dockerfile_copy_all method**

```python
def _check_dockerfile_copy_all(self, ctx: ProjectContext, dockerfile: Path) -> list[Finding]:
    """COPY . . without .dockerignore may send secrets to Docker daemon."""
    findings: list[Finding] = []
    try:
        content = dockerfile.read_text()
    except OSError:
        return findings

    # Look for COPY . . pattern
    if re.search(r"^COPY\s+\.\s+\.", content, re.MULTILINE):
        dockerignore = dockerfile.parent / ".dockerignore"
        if not dockerignore.exists():
            findings.append(
                Finding(
                    severity="warning",
                    file=str(dockerfile),
                    rule="V05-DOCKERFILE-COPY-ALL",
                    message="COPY . . without .dockerignore may leak secrets to build context",
                    fix=(
                        f"Create .dockerignore in {dockerfile.parent} to exclude "
                        f"sensitive files (.env, *.pem, *.key, etc.)"
                    ),
                )
            )
    return findings
```

- [ ] **Step 5: Commit dockerfile methods**

```bash
git add hooks/validators/docker_compose.py
git commit -m "feat: add dockerfile validation methods from V17"
```

### Task 5: Copy V17 Production Validation Methods

**Files:**
- Modify: `hooks/validators/docker_compose.py:600-900` (add production methods)

- [ ] **Step 1: Copy _check_prod_port_exposed method**

```python
def _check_prod_port_exposed(self, data: dict, compose_file: Path) -> list[Finding]:
    """Production compose should not expose host ports (use Traefik instead)."""
    findings: list[Finding] = []

    # Skip if this looks like dev/override file
    if any(keyword in str(compose_file) for keyword in ["override", "dev", "development"]):
        return findings

    for svc_name, svc_def in (data.get("services") or {}).items():
        if not isinstance(svc_def, dict):
            continue

        ports = svc_def.get("ports") or []
        if ports:
            findings.append(
                Finding(
                    severity="warning",
                    file=str(compose_file),
                    rule="V05-PROD-PORT-EXPOSED",
                    message=f"Service '{svc_name}' exposes ports in production config",
                    fix=(
                        f"Remove port mappings from production config and use "
                        f"Traefik labels instead for '{svc_name}'"
                    ),
                )
            )
    return findings
```

- [ ] **Step 2: Copy _check_prod_dev_mode method**

```python
def _check_prod_dev_mode(self, data: dict, compose_file: Path) -> list[Finding]:
    """Check for development mode flags in production config."""
    findings: list[Finding] = []

    # Skip if this looks like dev/override file
    if any(keyword in str(compose_file) for keyword in ["override", "dev", "development"]):
        return findings

    dev_patterns = [
        # Environment variables that shouldn't be true/enabled in prod
        (r"DEBUG\s*[=:]\s*(true|True|TRUE|1|yes|Yes|YES)", "DEBUG"),
        (r"NODE_ENV\s*[=:]\s*(development|dev)", "NODE_ENV"),
        (r"RAILS_ENV\s*[=:]\s*(development|dev)", "RAILS_ENV"),
        (r"ENVIRONMENT\s*[=:]\s*(development|dev)", "ENVIRONMENT"),
        (r"HASURA_GRAPHQL_DEV_MODE\s*[=:]\s*(true|True|TRUE|1|yes|Yes|YES)", "HASURA_GRAPHQL_DEV_MODE"),
    ]

    try:
        content = compose_file.read_text()
    except OSError:
        return findings

    for pattern, env_name in dev_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            line_num = len(content[:re.search(pattern, content, re.IGNORECASE).start()].split('\n'))
            findings.append(
                Finding(
                    severity="error",
                    file=str(compose_file),
                    rule="V05-PROD-DEV-MODE",
                    message=f"Development mode enabled: {env_name}",
                    fix=f"Set {env_name} to production value in {compose_file}",
                    line=line_num,
                )
            )
    return findings
```

- [ ] **Step 3: Copy _check_prod_wildcard_cors method**

```python
def _check_prod_wildcard_cors(self, data: dict, compose_file: Path) -> list[Finding]:
    """Check for wildcard CORS in production."""
    findings: list[Finding] = []

    # Skip if this looks like dev/override file
    if any(keyword in str(compose_file) for keyword in ["override", "dev", "development"]):
        return findings

    try:
        content = compose_file.read_text()
    except OSError:
        return findings

    cors_patterns = [
        r"CORS[_\w]*\s*[=:]\s*['\"]?\*['\"]?",
        r"ACCESS_CONTROL_ALLOW_ORIGIN\s*[=:]\s*['\"]?\*['\"]?",
    ]

    for pattern in cors_patterns:
        matches = re.finditer(pattern, content, re.IGNORECASE)
        for match in matches:
            line_num = content[:match.start()].count('\n') + 1
            findings.append(
                Finding(
                    severity="error",
                    file=str(compose_file),
                    rule="V05-PROD-WILDCARD-CORS",
                    message="CORS set to '*' (wildcard) in production",
                    fix="Set CORS to specific allowed origins instead of '*'",
                    line=line_num,
                )
            )
    return findings
```

- [ ] **Step 4: Commit production methods**

```bash
git add hooks/validators/docker_compose.py
git commit -m "feat: add production safety validation methods from V17"
```

### Task 6: Copy V17 Development Validation Methods

**Files:**
- Modify: `hooks/validators/docker_compose.py:900-1100` (add dev methods)

- [ ] **Step 1: Copy _check_dev_volume_mount method**

```python
def _check_dev_volume_mount(self, data: dict, compose_file: Path) -> list[Finding]:
    """Dev override should mount source code for hot reload."""
    findings: list[Finding] = []

    # Only check override files
    if not any(keyword in str(compose_file) for keyword in ["override", "dev"]):
        return findings

    for svc_name, svc_def in (data.get("services") or {}).items():
        if not isinstance(svc_def, dict):
            continue

        volumes = svc_def.get("volumes") or []
        has_source_mount = False

        for volume in volumes:
            volume_str = str(volume)
            # Look for source code mounts (./src, ./app, etc.)
            if re.search(r"\./[^/]+:/", volume_str) or ":/" in volume_str:
                has_source_mount = True
                break

        if not has_source_mount:
            findings.append(
                Finding(
                    severity="warning",
                    file=str(compose_file),
                    rule="V05-DEV-NO-VOLUME-MOUNT",
                    message=f"Dev service '{svc_name}' has no source code volume mounts",
                    fix=(
                        f"Add volume mount for hot reload: './src:/app/src' or similar "
                        f"to service '{svc_name}' in {compose_file}"
                    ),
                )
            )
    return findings
```

- [ ] **Step 2: Copy _check_dev_build_target method**

```python
def _check_dev_build_target(self, data: dict, compose_file: Path) -> list[Finding]:
    """Dev override should set build.target to 'dev'."""
    findings: list[Finding] = []

    # Only check override files
    if not any(keyword in str(compose_file) for keyword in ["override", "dev"]):
        return findings

    for svc_name, svc_def in (data.get("services") or {}).items():
        if not isinstance(svc_def, dict):
            continue

        build = svc_def.get("build")
        if isinstance(build, dict):
            target = build.get("target", "").lower()
            if target and target != "dev" and target != "development":
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(compose_file),
                        rule="V05-DEV-NO-BUILD-TARGET",
                        message=f"Service '{svc_name}' build target is '{target}', expected 'dev'",
                        fix=f"Set build.target to 'dev' for service '{svc_name}' in {compose_file}",
                    )
                )
    return findings
```

- [ ] **Step 3: Commit development methods**

```bash
git add hooks/validators/docker_compose.py
git commit -m "feat: add development validation methods from V17"
```

## Chunk 3: New Best Practice Validations

### Task 7: Implement New Validation Methods

**Files:**
- Modify: `hooks/validators/docker_compose.py:1100-1400` (add new methods)

- [ ] **Step 1: Implement _check_build_target_exists method**

```python
def _check_build_target_exists(self, ctx: ProjectContext, compose_files: list[Path], dockerfiles: list[Path]) -> list[Finding]:
    """Validate that docker-compose build.target references exist in Dockerfile."""
    findings: list[Finding] = []

    for compose_file in compose_files:
        try:
            data = yaml.safe_load(compose_file.read_text()) or {}
        except (yaml.YAMLError, OSError):
            continue

        services = data.get("services") or {}
        for svc_name, svc_def in services.items():
            if not isinstance(svc_def, dict):
                continue

            build = svc_def.get("build")
            if not isinstance(build, dict):
                continue

            target = build.get("target")
            if not target:
                continue

            # Find the dockerfile
            dockerfile_path = build.get("dockerfile", "Dockerfile")
            context_path = Path(build.get("context", "."))

            if context_path.is_absolute():
                dockerfile_full_path = context_path / dockerfile_path
            else:
                dockerfile_full_path = compose_file.parent / context_path / dockerfile_path

            # Check if dockerfile exists and has the target stage
            if dockerfile_full_path.exists():
                try:
                    dockerfile_content = dockerfile_full_path.read_text()
                    # Look for "FROM ... AS target_name"
                    stage_pattern = rf"^FROM\s+.*\s+AS\s+{re.escape(target)}\s*$"
                    if not re.search(stage_pattern, dockerfile_content, re.MULTILINE | re.IGNORECASE):
                        findings.append(
                            Finding(
                                severity="error",
                                file=str(compose_file),
                                rule="V05-BUILD-TARGET-MISSING",
                                message=f"Service '{svc_name}' targets stage '{target}' but Dockerfile has no such stage",
                                fix=(
                                    f"Add 'FROM base AS {target}' stage to {dockerfile_full_path} "
                                    f"or change build.target in {compose_file}"
                                ),
                            )
                        )
                except OSError:
                    pass  # Couldn't read dockerfile
    return findings
```

- [ ] **Step 2: Implement _check_base_image_latest method**

```python
def _check_base_image_latest(self, dockerfiles: list[Path]) -> list[Finding]:
    """Warn against using :latest tags or no tags (implicit latest)."""
    findings: list[Finding] = []

    for dockerfile in dockerfiles:
        try:
            content = dockerfile.read_text()
        except OSError:
            continue

        # Pattern 1: explicit :latest
        latest_pattern = r"^FROM\s+(\S+):latest\b"
        for match in re.finditer(latest_pattern, content, re.MULTILINE):
            line_num = content[:match.start()].count('\n') + 1
            image_name = match.group(1)
            findings.append(
                Finding(
                    severity="warning",
                    file=str(dockerfile),
                    rule="V05-BASE-IMAGE-LATEST",
                    message=f"Using ':latest' tag for base image '{image_name}' - not recommended for production",
                    fix=f"Use specific version tag like '{image_name}:20-slim' instead of ':latest'",
                    line=line_num,
                )
            )

        # Pattern 2: no tag (implicit latest)
        notag_pattern = r"^FROM\s+([^:\s]+)\s+(?:AS\s+\w+\s*)?$"
        for match in re.finditer(notag_pattern, content, re.MULTILINE):
            line_num = content[:match.start()].count('\n') + 1
            image_name = match.group(1)
            # Skip scratch, multi-stage references
            if image_name.lower() in ("scratch",) or "/" not in image_name:
                continue
            findings.append(
                Finding(
                    severity="warning",
                    file=str(dockerfile),
                    rule="V05-BASE-IMAGE-LATEST",
                    message=f"No tag specified for base image '{image_name}' (defaults to :latest)",
                    fix=f"Add specific version tag like '{image_name}:20-slim'",
                    line=line_num,
                )
            )
    return findings
```

- [ ] **Step 3: Implement _check_dockerignore_exists method**

```python
def _check_dockerignore_exists(self, ctx: ProjectContext, dockerfiles: list[Path]) -> list[Finding]:
    """Ensure .dockerignore exists when COPY . . is used."""
    findings: list[Finding] = []

    for dockerfile in dockerfiles:
        try:
            content = dockerfile.read_text()
        except OSError:
            continue

        # Check if dockerfile uses COPY . . pattern
        if re.search(r"^COPY\s+\.\s+\.", content, re.MULTILINE):
            dockerignore = dockerfile.parent / ".dockerignore"
            if not dockerignore.exists():
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(dockerfile),
                        rule="V05-MISSING-DOCKERIGNORE",
                        message=f"Dockerfile uses 'COPY . .' but no .dockerignore found",
                        fix=(
                            f"Create .dockerignore in {dockerfile.parent} to exclude "
                            f"unnecessary files (node_modules, .git, .env, etc.)"
                        ),
                    )
                )
    return findings
```

- [ ] **Step 4: Commit new validation methods**

```bash
git add hooks/validators/docker_compose.py
git commit -m "feat: add 3 new validation methods from DOCKER_BEST_PRACTICES.md"
```

### Task 8: Verify Complete Implementation

**Files:**
- Test: `hooks/validators/docker_compose.py` (basic syntax check)

- [ ] **Step 1: Run syntax check**

```bash
python -m py_compile hooks/validators/docker_compose.py
```

Expected: No syntax errors

- [ ] **Step 2: Run basic import test**

```bash
cd /Users/taehyun/github/HwangTaehyun/verifiers
python -c "from hooks.validators.docker_compose import DockerValidator; print('Import successful')"
```

Expected: "Import successful"

- [ ] **Step 3: Count validation methods**

```bash
grep -c "def _check_" hooks/validators/docker_compose.py
```

Expected: 17 (5 original + 9 from V17 + 3 new)

- [ ] **Step 4: Commit verification milestone**

```bash
git add .
git commit -m "milestone: V05 expanded to 17 validation rules"
```

## Chunk 4: Test Migration and Integration

### Task 9: Migrate V17 Tests to V05

**Files:**
- Modify: `tests/test_docker_compose.py:1000-2000` (add V17 test content)
- Read: `tests/test_docker_prod_deploy.py` (source for migration)

- [ ] **Step 1: Copy V17 test imports and fixtures**

Add to top of `test_docker_compose.py`:

```python
# Additional imports for V17 functionality
import re
from pathlib import Path
```

- [ ] **Step 2: Copy V17 test classes with V05 rule IDs**

Copy all test classes from `test_docker_prod_deploy.py` and replace:
- `DockerProdDeployValidator` → `DockerValidator`
- `V17-` → `V05-`
- Update test class names to avoid conflicts

```python
class TestDockerfileValidation:
    """Test Dockerfile validation methods migrated from V17."""

    def test_dockerfile_multistage_single_stage_warns(
        self, validator: DockerValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """Single stage Dockerfile should warn about missing multi-stage."""
        dockerfile = tmp_project / "Dockerfile"
        dockerfile.write_text("FROM node:20\nCOPY . .\nRUN npm install")

        findings = validator._check_dockerfile_multistage(dockerfile)

        assert len(findings) == 1
        f = findings[0]
        assert f.rule == "V05-DOCKERFILE-NO-MULTISTAGE"
        assert f.severity == "warning"
        assert "multi-stage" in f.message

    # ... continue with all other V17 test methods
```

- [ ] **Step 3: Add new validation rule tests**

```python
class TestNewBestPracticeValidation:
    """Test new validation rules from DOCKER_BEST_PRACTICES.md."""

    def test_build_target_missing_errors(
        self, validator: DockerValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """build.target that doesn't exist in Dockerfile should error."""
        compose_file = tmp_project / "docker-compose.yaml"
        compose_file.write_text("""
version: '3'
services:
  app:
    build:
      context: .
      target: nonexistent
""")
        dockerfile = tmp_project / "Dockerfile"
        dockerfile.write_text("FROM node:20 AS dev\nFROM node:20-slim AS prod")

        findings = validator._check_build_target_exists(project_ctx, [compose_file], [dockerfile])

        assert len(findings) == 1
        f = findings[0]
        assert f.rule == "V05-BUILD-TARGET-MISSING"
        assert f.severity == "error"
        assert "nonexistent" in f.message

    def test_base_image_latest_warns(
        self, validator: DockerValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """Using :latest tag should warn."""
        dockerfile = tmp_project / "Dockerfile"
        dockerfile.write_text("FROM node:latest\nFROM alpine:latest AS prod")

        findings = validator._check_base_image_latest([dockerfile])

        assert len(findings) == 2
        assert all(f.rule == "V05-BASE-IMAGE-LATEST" for f in findings)
        assert all(f.severity == "warning" for f in findings)

    def test_dockerignore_missing_warns(
        self, validator: DockerValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """COPY . . without .dockerignore should warn."""
        dockerfile = tmp_project / "Dockerfile"
        dockerfile.write_text("FROM node:20\nCOPY . .")

        findings = validator._check_dockerignore_exists(project_ctx, [dockerfile])

        assert len(findings) == 1
        f = findings[0]
        assert f.rule == "V05-MISSING-DOCKERIGNORE"
        assert f.severity == "warning"
        assert "COPY . ." in f.message
```

- [ ] **Step 4: Update existing V05 tests**

Update `DockerComposeValidator` → `DockerValidator` in existing tests:

```python
@pytest.fixture
def validator() -> DockerValidator:
    """Create validator instance for testing."""
    return DockerValidator()
```

- [ ] **Step 5: Run test migration verification**

```bash
python -m pytest tests/test_docker_compose.py -v --tb=short
```

Expected: All tests pass, significant increase in test count

- [ ] **Step 6: Commit test integration**

```bash
git add tests/test_docker_compose.py
git commit -m "feat: migrate and integrate V17 tests into V05 test suite"
```

### Task 10: Full Test Suite Validation

**Files:**
- Test: `tests/test_docker_compose.py` (comprehensive test run)
- Test: `hooks/validators/docker_compose.py` (integration testing)

- [ ] **Step 1: Run comprehensive test suite**

```bash
python -m pytest tests/test_docker_compose.py -v -x
```

Expected: All tests pass, 17 validation rules covered

- [ ] **Step 2: Test real-world integration**

```bash
# Create a test project with various Docker files
mkdir -p /tmp/docker_test/{server,web}
echo "FROM node:latest" > /tmp/docker_test/server/Dockerfile
echo "version: '3'\nservices:\n  app:\n    ports: ['8080:80', '8080:3000']" > /tmp/docker_test/docker-compose.yaml

cd /tmp/docker_test
python /Users/taehyun/github/HwangTaehyun/verifiers/hooks/validators/docker_compose.py
```

Expected: Multiple validation findings for port conflicts, latest tags, etc.

- [ ] **Step 3: Test performance**

```bash
time python -m pytest tests/test_docker_compose.py
```

Expected: Reasonable execution time (< 30 seconds)

- [ ] **Step 4: Commit test validation**

```bash
git add .
git commit -m "test: validate consolidated Docker validator functionality"
```

## Chunk 5: Cleanup and Documentation

### Task 11: Delete V17 Files and Update Skills

**Files:**
- Delete: `hooks/validators/docker_prod_deploy.py`
- Delete: `tests/test_docker_prod_deploy.py`
- Modify: `/Users/taehyun/.claude/skills/verify-docker/SKILL.md`

- [ ] **Step 1: Verify no direct V17 usage**

```bash
grep -r "V17\|docker_prod_deploy" . --exclude-dir=.git --exclude="*.backup"
```

Expected: Only references in backup files and logs

- [ ] **Step 2: Delete V17 files**

```bash
rm hooks/validators/docker_prod_deploy.py
rm tests/test_docker_prod_deploy.py
```

- [ ] **Step 3: Update verify-docker skill description**

Update `/Users/taehyun/.claude/skills/verify-docker/SKILL.md`:

```markdown
---
name: verify-docker
description: Comprehensive Docker validation - 17 rules covering Compose files, Dockerfiles, production safety, and best practices
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/docker_compose.py"
          timeout: 15
---

## Comprehensive Docker Verification Activated

All Docker-related files validated: `docker-compose*.yaml`, `Dockerfile*`

### 17 Validation Rules

**Compose Files (5 rules):**
- **V05-PORT-CONFLICT**: Service port conflicts
- **V05-VHOST-NO-NETWORK**: VIRTUAL_HOST without nginx-proxy network
- **V05-UNDEFINED-NETWORK**: Referenced network not defined
- **V05-MISSING-HEALTHCHECK**: depends_on service_healthy without healthcheck
- **V05-MISSING-ENV-VAR**: ${VAR} reference without .env definition

**Dockerfile Best Practices (4 rules):**
- **V05-DOCKERFILE-NO-USER**: Production stage runs as root
- **V05-DOCKERFILE-NO-EXPOSE**: Missing EXPOSE directive
- **V05-DOCKERFILE-COPY-ALL**: COPY . . without .dockerignore
- **V05-DOCKERFILE-NO-MULTISTAGE**: Single-stage Dockerfile

**Production Safety (3 rules):**
- **V05-PROD-PORT-EXPOSED**: Production ports exposed (use Traefik)
- **V05-PROD-DEV-MODE**: Dev mode enabled in production
- **V05-PROD-WILDCARD-CORS**: CORS set to "*" in production

**Development Setup (2 rules):**
- **V05-DEV-NO-VOLUME-MOUNT**: Dev override missing source mounts
- **V05-DEV-NO-BUILD-TARGET**: Dev override missing build target

**Advanced Best Practices (3 rules):**
- **V05-BUILD-TARGET-MISSING**: build.target doesn't exist in Dockerfile
- **V05-BASE-IMAGE-LATEST**: Using latest tag (not recommended)
- **V05-MISSING-DOCKERIGNORE**: .dockerignore missing with COPY . .
```

- [ ] **Step 4: Clean up log files**

```bash
rm -f logs/V17-docker-prod-deploy.jsonl
```

- [ ] **Step 5: Commit cleanup**

```bash
git add -A
git commit -m "cleanup: remove V17 files and update verify-docker skill description"
```

### Task 12: Final Integration Test

**Files:**
- Test: Complete system integration test

- [ ] **Step 1: Run verify-docker skill test**

Create test files and run the skill:

```bash
mkdir -p /tmp/skill_test
cd /tmp/skill_test

# Create problematic Docker files
echo "FROM node:latest\nCOPY . ." > Dockerfile
echo "version: '3'\nservices:\n  app:\n    ports: ['8080:80', '8080:80']\n  web:\n    ports: ['8080:3000']" > docker-compose.yaml

# Simulate skill execution
echo '{"tool_name": "Edit", "tool_input": {"file_path": "docker-compose.yaml"}, "cwd": "/tmp/skill_test"}' | \
python /Users/taehyun/github/HwangTaehyun/verifiers/hooks/validators/docker_compose.py
```

Expected: Multiple validation findings covering different rule categories

- [ ] **Step 2: Verify 17 rules operational**

Count distinct rule IDs in output:

```bash
# Should show all 17 V05 rule types
grep -o 'V05-[A-Z-]*' /tmp/skill_test_output.json | sort -u | wc -l
```

Expected: Multiple distinct V05 rules found

- [ ] **Step 3: Performance benchmark**

```bash
time python /Users/taehyun/github/HwangTaehyun/verifiers/hooks/validators/docker_compose.py < test_input.json
```

Expected: Completes in < 5 seconds

- [ ] **Step 4: Final commit and tag**

```bash
git add .
git commit -m "feat: Docker validator consolidation complete - 17 unified validation rules

- Consolidated V05 + V17 + 3 new DOCKER_BEST_PRACTICES.md rules
- Single comprehensive validator replacing separate V05/V17
- Updated verify-docker skill with complete rule documentation
- Comprehensive test suite with all validation scenarios"

git tag -a v1.0-docker-consolidation -m "Docker validator consolidation milestone"
```

## Success Criteria Checklist

- [ ] **Functionality**: All 17 validation rules operational
- [ ] **Integration**: verify-docker skill works with consolidated validator
- [ ] **Testing**: Comprehensive test coverage maintained
- [ ] **Performance**: No significant performance degradation
- [ ] **Documentation**: Skills and rules properly documented
- [ ] **Cleanup**: V17 files removed, no orphaned references
- [ ] **Backwards compatibility**: All existing V05 functionality preserved

---

**Plan complete and ready for execution using superpowers:subagent-driven-development or superpowers:executing-plans.**