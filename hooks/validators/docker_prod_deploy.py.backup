"""V17: Docker Production Deployment validator — Dockerfile + compose production/dev readiness.

Checks:
  Dockerfile Best Practices:
    V17-DOCKERFILE-NO-USER:       Production stage runs as root (missing USER directive)
    V17-DOCKERFILE-NO-EXPOSE:     Missing EXPOSE directive in production stage
    V17-DOCKERFILE-COPY-ALL:      COPY . . without .dockerignore may leak secrets
    V17-DOCKERFILE-NO-MULTISTAGE: Single-stage Dockerfile (no multi-stage build)

  Production Compose:
    V17-PROD-PORT-EXPOSED:        Production compose should not expose host ports (use Traefik)
    V17-PROD-DEV-MODE:            Dev mode enabled in production config
    V17-PROD-WILDCARD-CORS:       CORS set to "*" in production
    V17-PROD-NO-TRAEFIK-LABELS:   Service has no Traefik labels for domain routing
    V17-PROD-NO-RESOURCE-LIMITS:  No resource limits (deploy.resources.limits) in production

  Dev Override Compose:
    V17-DEV-NO-VOLUME-MOUNT:      Dev override should mount source code for hot reload
    V17-DEV-NO-BUILD-TARGET:      Dev override should set build.target to 'dev'
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, ValidationResult, read_hook_input, write_hook_output
from lib.project_context import ProjectContext


class DockerProdDeployValidator(BaseValidator):
    """V17: Docker Production Deployment Validator."""

    id = "V17-docker-prod-deploy"
    name = "Docker Production Deployment Validator"
    file_patterns: list[str] = [
        "**/docker-compose*.yaml",
        "**/docker-compose*.yml",
        "**/Dockerfile*",
        "**/*.Dockerfile",
        "**/.dockerignore",
    ]

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []

        # ── Dockerfile checks ─────────────────────────────────────────
        dockerfiles = list(ctx.project_root.glob("**/Dockerfile*"))
        dockerfiles.extend(ctx.project_root.glob("**/*.Dockerfile"))
        dockerfiles = self._exclude_paths(dockerfiles)

        for dockerfile in dockerfiles:
            findings.extend(self._check_dockerfile_multistage(dockerfile))
            findings.extend(self._check_dockerfile_user(dockerfile))
            findings.extend(self._check_dockerfile_expose(dockerfile))
            findings.extend(self._check_dockerfile_copy_all(ctx, dockerfile))

        # ── Compose checks ────────────────────────────────────────────
        compose_files = list(ctx.project_root.glob("**/docker-compose*.yaml"))
        compose_files.extend(ctx.project_root.glob("**/docker-compose*.yml"))
        compose_files = self._exclude_paths(compose_files)

        for compose_file in compose_files:
            try:
                data = yaml.safe_load(compose_file.read_text()) or {}
            except (yaml.YAMLError, OSError):
                continue

            fname = compose_file.name

            if "production" in fname:
                findings.extend(self._check_prod_port_exposed(data, compose_file))
                findings.extend(self._check_prod_dev_mode(data, compose_file))
                findings.extend(self._check_prod_wildcard_cors(data, compose_file))
                findings.extend(self._check_prod_traefik_labels(data, compose_file))
                findings.extend(self._check_prod_resource_limits(data, compose_file))
            elif "override" in fname:
                findings.extend(self._check_dev_volume_mount(data, compose_file))
                findings.extend(self._check_dev_build_target(data, compose_file))

        return ValidationResult(validator_id=self.id, findings=findings)

    @staticmethod
    def _exclude_paths(files: list[Path]) -> list[Path]:
        """Exclude vendor, node_modules, .git directories."""
        exclude = {"vendor", "node_modules", ".git", "__pycache__", ".venv"}
        return [f for f in files if not any(p in str(f) for p in exclude)]

    # ══════════════════════════════════════════════════════════════════
    # Dockerfile checks
    # ══════════════════════════════════════════════════════════════════

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
                    rule="V17-DOCKERFILE-NO-MULTISTAGE",
                    message="Dockerfile has only one FROM stage (no multi-stage build)",
                    fix=(
                        f"Use multi-stage build in {dockerfile.name}: "
                        f"dev stage (hot reload), builder stage (compile), "
                        f"prod stage (minimal runtime like alpine)"
                    ),
                )
            )
        return findings

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
                        rule="V17-DOCKERFILE-NO-USER",
                        message=f"Production stage runs as root (missing USER directive)",
                        fix=(
                            f"Add a non-root user to the production stage in {dockerfile.name}: "
                            f"RUN addgroup -S app && adduser -S app -G app, then USER app"
                        ),
                    )
                )
        return findings

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
                    rule="V17-DOCKERFILE-NO-EXPOSE",
                    message="No EXPOSE directive found in Dockerfile",
                    fix=f"Add EXPOSE <port> to {dockerfile.name} to document the container port",
                )
            )
        return findings

    def _check_dockerfile_copy_all(self, ctx: ProjectContext, dockerfile: Path) -> list[Finding]:
        """COPY . . without .dockerignore may send secrets to Docker daemon."""
        findings: list[Finding] = []
        try:
            content = dockerfile.read_text()
        except OSError:
            return findings

        has_copy_all = bool(re.search(r"^COPY\s+\.\s+\.", content, re.MULTILINE))
        if not has_copy_all:
            return findings

        # Check if .dockerignore exists in the same directory
        dockerignore = dockerfile.parent / ".dockerignore"
        if not dockerignore.exists():
            findings.append(
                Finding(
                    severity="warning",
                    file=str(dockerfile),
                    rule="V17-DOCKERFILE-COPY-ALL",
                    message="COPY . . used but no .dockerignore found — secrets may leak to Docker daemon",
                    fix=(
                        f"Create {dockerfile.parent}/.dockerignore to exclude "
                        f".env, .git, node_modules, and other sensitive files"
                    ),
                )
            )
        return findings

    # ══════════════════════════════════════════════════════════════════
    # Production Compose checks
    # ══════════════════════════════════════════════════════════════════

    def _check_prod_port_exposed(self, data: dict, compose_file: Path) -> list[Finding]:
        """Production compose should not expose host ports (use reverse proxy)."""
        findings: list[Finding] = []

        for svc_name, svc_def in (data.get("services") or {}).items():
            if not isinstance(svc_def, dict):
                continue

            ports = svc_def.get("ports")
            if ports is None:
                continue

            # Allow !override [] (empty list) — that's the correct pattern
            if isinstance(ports, list) and len(ports) > 0:
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(compose_file),
                        rule="V17-PROD-PORT-EXPOSED",
                        message=f"Service '{svc_name}' exposes host ports in production compose",
                        fix=(
                            f"Remove ports from '{svc_name}' in {compose_file.name} "
                            f"or use 'ports: !override []' to route through Traefik instead"
                        ),
                    )
                )

        return findings

    def _check_prod_dev_mode(self, data: dict, compose_file: Path) -> list[Finding]:
        """Production compose should not have dev mode enabled."""
        findings: list[Finding] = []

        for svc_name, svc_def in (data.get("services") or {}).items():
            if not isinstance(svc_def, dict):
                continue

            env = svc_def.get("environment") or {}
            if isinstance(env, list):
                env = dict(e.split("=", 1) for e in env if "=" in e)

            for key, val in env.items():
                val_str = str(val).lower().strip('"').strip("'")
                # Check for common dev mode flags set to true
                if key.upper() in ("APP_DEV", "DEV", "DEBUG", "NODE_ENV") and val_str in (
                    "true",
                    "1",
                    "yes",
                    "development",
                ):
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(compose_file),
                            rule="V17-PROD-DEV-MODE",
                            message=(
                                f"Service '{svc_name}' has dev mode enabled: {key}={val_str}"
                            ),
                            fix=(
                                f"Set '{key}' to 'false' (or 'production' for NODE_ENV) "
                                f"in service '{svc_name}' in {compose_file.name}"
                            ),
                        )
                    )

                # Check Hasura dev mode
                if key == "HASURA_GRAPHQL_DEV_MODE" and val_str == "true":
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(compose_file),
                            rule="V17-PROD-DEV-MODE",
                            message=f"Service '{svc_name}' has Hasura dev mode enabled in production",
                            fix=(
                                f"Set HASURA_GRAPHQL_DEV_MODE to 'false' "
                                f"in service '{svc_name}' in {compose_file.name}"
                            ),
                        )
                    )

        return findings

    def _check_prod_wildcard_cors(self, data: dict, compose_file: Path) -> list[Finding]:
        """Production compose should not use wildcard CORS origins."""
        findings: list[Finding] = []

        for svc_name, svc_def in (data.get("services") or {}).items():
            if not isinstance(svc_def, dict):
                continue

            env = svc_def.get("environment") or {}
            if isinstance(env, list):
                env = dict(e.split("=", 1) for e in env if "=" in e)

            for key, val in env.items():
                if "CORS" in key.upper() and str(val).strip('"').strip("'") == "*":
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(compose_file),
                            rule="V17-PROD-WILDCARD-CORS",
                            message=f"Service '{svc_name}' has wildcard CORS '{key}=*' in production",
                            fix=(
                                f"Restrict {key} to production domains "
                                f"(e.g., 'https://example.com') in {compose_file.name}"
                            ),
                        )
                    )

        return findings

    def _check_prod_traefik_labels(self, data: dict, compose_file: Path) -> list[Finding]:
        """Services in production should have Traefik routing labels."""
        findings: list[Finding] = []

        for svc_name, svc_def in (data.get("services") or {}).items():
            if not isinstance(svc_def, dict):
                continue

            # Skip infrastructure services (DB, cache) that don't need external access
            infra_services = {"postgres", "redis", "neo4j", "minio", "hasura"}
            if svc_name.lower() in infra_services:
                continue

            labels = svc_def.get("labels")
            if labels is None:
                continue

            # !override [] explicitly removes labels — that's ok for infra services
            if isinstance(labels, list) and len(labels) == 0:
                continue

            # Check if Traefik is enabled
            if isinstance(labels, list):
                has_traefik = any("traefik.enable=true" in str(l) for l in labels)
                has_router = any("traefik.http.routers" in str(l) for l in labels)
                if has_traefik and not has_router:
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(compose_file),
                            rule="V17-PROD-NO-TRAEFIK-LABELS",
                            message=(
                                f"Service '{svc_name}' has traefik.enable=true "
                                f"but no router/service label for domain routing"
                            ),
                            fix=(
                                f"Add traefik.http.routers and traefik.http.services labels "
                                f"to '{svc_name}' in {compose_file.name} for production domain routing"
                            ),
                        )
                    )

        return findings

    def _check_prod_resource_limits(self, data: dict, compose_file: Path) -> list[Finding]:
        """Production services should have resource limits."""
        findings: list[Finding] = []

        for svc_name, svc_def in (data.get("services") or {}).items():
            if not isinstance(svc_def, dict):
                continue

            # Only check application services with a build section
            if "build" not in svc_def and "image" not in svc_def:
                continue

            deploy = svc_def.get("deploy") or {}
            resources = deploy.get("resources") or {}
            limits = resources.get("limits") or {}

            if not limits:
                findings.append(
                    Finding(
                        severity="info",
                        file=str(compose_file),
                        rule="V17-PROD-NO-RESOURCE-LIMITS",
                        message=f"Service '{svc_name}' has no resource limits in production",
                        fix=(
                            f"Add deploy.resources.limits (cpus, memory) to "
                            f"'{svc_name}' in {compose_file.name} to prevent resource starvation"
                        ),
                    )
                )

        return findings

    # ══════════════════════════════════════════════════════════════════
    # Dev Override Compose checks
    # ══════════════════════════════════════════════════════════════════

    def _check_dev_volume_mount(self, data: dict, compose_file: Path) -> list[Finding]:
        """Dev override should mount source code for hot reload."""
        findings: list[Finding] = []

        for svc_name, svc_def in (data.get("services") or {}).items():
            if not isinstance(svc_def, dict):
                continue

            # Only check services that have a build section (application services)
            build = svc_def.get("build")
            if not build:
                continue

            volumes = svc_def.get("volumes") or []
            if not volumes:
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(compose_file),
                        rule="V17-DEV-NO-VOLUME-MOUNT",
                        message=(
                            f"Service '{svc_name}' in dev override has no volume mounts "
                            f"for source code hot reload"
                        ),
                        fix=(
                            f"Add volume mounts to '{svc_name}' in {compose_file.name} "
                            f"to enable hot reload (e.g., './src:/app/src:ro' or '.:/app')"
                        ),
                    )
                )

        return findings

    def _check_dev_build_target(self, data: dict, compose_file: Path) -> list[Finding]:
        """Dev override should set build target to 'dev'."""
        findings: list[Finding] = []

        for svc_name, svc_def in (data.get("services") or {}).items():
            if not isinstance(svc_def, dict):
                continue

            build = svc_def.get("build")
            if not build:
                continue

            if isinstance(build, dict):
                target = build.get("target", "")
                if target and target.lower() != "dev":
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(compose_file),
                            rule="V17-DEV-NO-BUILD-TARGET",
                            message=(
                                f"Service '{svc_name}' in dev override has build target '{target}' "
                                f"instead of 'dev'"
                            ),
                            fix=(
                                f"Set build.target to 'dev' for '{svc_name}' in "
                                f"{compose_file.name} to use the development stage with hot reload"
                            ),
                        )
                    )

        return findings


# ── Standalone execution ─────────────────────────────────────────────────────


def main() -> None:
    """Run as standalone PostToolUse hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        write_hook_output({})
        return

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    cwd = input_data.get("cwd", ".")

    if not file_path:
        write_hook_output({})
        return

    ctx = ProjectContext(cwd)
    validator = DockerProdDeployValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
