"""V05: Docker Compose validator — ports, networks, healthchecks, env vars.

Checks:
  V05-PORT-CONFLICT: Two services mapping same host port
  V05-VHOST-NO-NETWORK: VIRTUAL_HOST set but not on nginx-proxy network
  V05-UNDEFINED-NETWORK: Service references network not defined in top-level
  V05-MISSING-HEALTHCHECK: depends_on condition: service_healthy but no healthcheck
  V05-MISSING-ENV-VAR: ${VAR} referenced without default and not in .env
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

      Production Safety (5 rules):
        V05-PROD-PORT-EXPOSED: Production compose should not expose host ports
        V05-PROD-DEV-MODE: Dev mode enabled in production config
        V05-PROD-WILDCARD-CORS: CORS set to "*" in production
        V05-PROD-TRAEFIK-LABELS: Service missing Traefik labels
        V05-PROD-RESOURCE-LIMITS: No resource limits in production

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
            findings.extend(self._check_prod_traefik_labels(data, compose_file))
            findings.extend(self._check_prod_resource_limits(data, compose_file))
            findings.extend(self._check_dev_volume_mount(data, compose_file))
            findings.extend(self._check_dev_build_target(data, compose_file))

        # Validate dockerfiles (V17 checks + new DOCKER_BEST_PRACTICES.md rules)
        for dockerfile in dockerfiles:
            findings.extend(self._check_dockerfile_multistage(dockerfile))
            findings.extend(self._check_dockerfile_user(dockerfile))
            findings.extend(self._check_dockerfile_expose(dockerfile))
            findings.extend(self._check_dockerfile_copy_all(ctx, dockerfile))
            findings.extend(self._check_base_image_latest(dockerfile))
            findings.extend(self._check_dockerignore_exists(dockerfile))

        # Cross-file validations
        findings.extend(self._check_build_target_exists(compose_files))

        return ValidationResult(validator_id=self.id, findings=findings)

    def _filter_excluded_files(self, files: list[Path]) -> list[Path]:
        """Exclude vendor, node_modules, .git directories."""
        exclude = {"vendor", "node_modules", ".git", "__pycache__", ".venv"}
        return [f for f in files if not any(p in str(f) for p in exclude)]

    # ── Check 1: Port conflicts ──────────────────────────────────────────

    def _check_port_conflicts(self, data: dict, compose_file: Path) -> list[Finding]:
        """Host port mappings must not collide across services."""
        findings: list[Finding] = []
        port_map: dict[str, str] = {}  # {host_port: service_name}

        for svc_name, svc_def in (data.get("services") or {}).items():
            if not isinstance(svc_def, dict):
                continue
            for port in svc_def.get("ports") or []:
                port_str = str(port)
                if ":" in port_str:
                    # Extract host port (before first colon)
                    host_port = port_str.split(":")[0].strip()
                    # Strip IP binding if present (e.g., "127.0.0.1:5432")
                    if "." in host_port:
                        host_port = host_port.rsplit(".", 1)[-1]

                    if host_port in port_map and port_map[host_port] != svc_name:
                        container_port = port_str.split(":")[-1]
                        findings.append(
                            Finding(
                                severity="error",
                                file=str(compose_file),
                                rule="V05-PORT-CONFLICT",
                                message=(
                                    f"Host port {host_port} used by both '{port_map[host_port]}' and '{svc_name}'"
                                ),
                                fix=(
                                    f"Change the host port for '{svc_name}' to an unused port "
                                    f"(e.g., {int(host_port) + 1}:{container_port})"
                                ),
                            )
                        )
                    port_map[host_port] = svc_name

        return findings

    # ── Check 2: VIRTUAL_HOST ↔ nginx-proxy network ─────────────────────

    def _check_virtual_host_network(self, data: dict, compose_file: Path) -> list[Finding]:
        """Services with VIRTUAL_HOST must be on nginx-proxy network."""
        findings: list[Finding] = []

        for svc_name, svc_def in (data.get("services") or {}).items():
            if not isinstance(svc_def, dict):
                continue

            env = svc_def.get("environment") or {}
            if isinstance(env, list):
                env = dict(e.split("=", 1) for e in env if "=" in e)

            has_virtual_host = "VIRTUAL_HOST" in env

            svc_networks = svc_def.get("networks") or []
            if isinstance(svc_networks, dict):
                svc_networks = list(svc_networks.keys())

            on_nginx = "nginx-proxy" in svc_networks

            if has_virtual_host and not on_nginx:
                findings.append(
                    Finding(
                        severity="error",
                        file=str(compose_file),
                        rule="V05-VHOST-NO-NETWORK",
                        message=(f"Service '{svc_name}' has VIRTUAL_HOST but is not on nginx-proxy network"),
                        fix=(f"Add 'nginx-proxy' to the networks list of service '{svc_name}' in {compose_file}"),
                    )
                )

        return findings

    # ── Check 3: Network reference validity ──────────────────────────────

    def _check_network_references(self, data: dict, compose_file: Path) -> list[Finding]:
        """Service network references must be defined in top-level networks."""
        findings: list[Finding] = []
        defined_networks = set((data.get("networks") or {}).keys())

        for svc_name, svc_def in (data.get("services") or {}).items():
            if not isinstance(svc_def, dict):
                continue

            svc_nets = svc_def.get("networks") or []
            if isinstance(svc_nets, dict):
                svc_nets = list(svc_nets.keys())

            for net in svc_nets:
                if net not in defined_networks:
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(compose_file),
                            rule="V05-UNDEFINED-NETWORK",
                            message=(f"Service '{svc_name}' references network '{net}' which is not defined"),
                            fix=(
                                f"Add '{net}' to the top-level 'networks:' section in "
                                f"{compose_file} with 'external: true' if it's created elsewhere"
                            ),
                        )
                    )

        return findings

    # ── Check 4: depends_on ↔ healthcheck ────────────────────────────────

    def _check_depends_on_healthcheck(self, data: dict, compose_file: Path) -> list[Finding]:
        """depends_on condition: service_healthy requires healthcheck definition."""
        findings: list[Finding] = []
        services = data.get("services") or {}

        for svc_name, svc_def in services.items():
            if not isinstance(svc_def, dict):
                continue

            deps = svc_def.get("depends_on") or {}
            if isinstance(deps, dict):
                for dep_name, dep_config in deps.items():
                    if isinstance(dep_config, dict) and dep_config.get("condition") == "service_healthy":
                        dep_svc = services.get(dep_name, {})
                        if isinstance(dep_svc, dict) and "healthcheck" not in dep_svc:
                            findings.append(
                                Finding(
                                    severity="error",
                                    file=str(compose_file),
                                    rule="V05-MISSING-HEALTHCHECK",
                                    message=(
                                        f"'{svc_name}' depends on '{dep_name}' with "
                                        f"condition: service_healthy, but '{dep_name}' "
                                        f"has no healthcheck"
                                    ),
                                    fix=(f"Add a healthcheck to service '{dep_name}' in {compose_file}"),
                                )
                            )

        return findings

    # ── Check 5: Environment variable references ─────────────────────────

    def _check_env_var_references(self, ctx: ProjectContext, data: dict, compose_file: Path) -> list[Finding]:
        """${VAR} without default should exist in .env file."""
        findings: list[Finding] = []

        # Load .env variables
        env_file = ctx.project_root / ".env"
        env_vars: set[str] = set()
        if env_file.exists():
            try:
                for line in env_file.read_text().splitlines():
                    if "=" in line and not line.strip().startswith("#"):
                        env_vars.add(line.split("=", 1)[0].strip())
            except OSError:
                pass

        # Also check .env.example
        env_example = ctx.project_root / ".env.example"
        if env_example.exists():
            try:
                for line in env_example.read_text().splitlines():
                    if "=" in line and not line.strip().startswith("#"):
                        env_vars.add(line.split("=", 1)[0].strip())
            except OSError:
                pass

        # Scan compose file text for ${VAR} references
        try:
            content = compose_file.read_text()
        except OSError:
            return findings

        # Match ${VAR} but not ${VAR:-default}
        for match in re.finditer(r"\$\{(\w+)\}", content):
            var = match.group(1)
            start = match.start()

            # Check if this specific reference has a :- default
            full_ref = content[start : start + len(match.group(0)) + 30]
            close_brace = full_ref.find("}")
            if close_brace > 0:
                ref_content = full_ref[:close_brace]
                if ":-" in ref_content:
                    continue  # Has default

            if var not in env_vars:
                line_num = content[:start].count("\n") + 1
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(compose_file),
                        rule="V05-MISSING-ENV-VAR",
                        message=f"${{{var}}} referenced without default, but not in .env",
                        fix=(f"Add '{var}=<value>' to {ctx.project_root}/.env or use '${{{var}:-default}}' syntax"),
                        line=line_num,
                    )
                )

        return findings

    # ── V17 Dockerfile Methods ──────────────────────────────────────────────

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

    def _check_dockerfile_user(self, dockerfile: Path) -> list[Finding]:
        """Production stage should not run as root (must have USER directive)."""
        findings: list[Finding] = []

        # Skip third-party base images that must run as root
        fname = dockerfile.name.lower()
        if "hasura" in fname:
            return findings

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
                    rule="V05-DOCKERFILE-COPY-ALL",
                    message="COPY . . used but no .dockerignore found — secrets may leak to Docker daemon",
                    fix=(
                        f"Create {dockerfile.parent}/.dockerignore to exclude "
                        f".env, .git, node_modules, and other sensitive files"
                    ),
                )
            )
        return findings

    # ── V17 Production Methods ──────────────────────────────────────────────

    @staticmethod
    def _is_dev_intended_compose(compose_file: Path) -> bool:
        """True when this compose file is meant for local dev, not production.

        Convention:
          - docker-compose.yaml / .yml → base (dev defaults on most projects)
          - *override*.yaml             → auto-loaded dev layer
          - docker-compose.production.yaml / .prod.yaml → explicit prod target

        Production-only checks (port exposure, resource limits, dev-mode
        flags) skip dev-intended files so local setups can bind host ports
        without spraying warnings.
        """
        fname = compose_file.name.lower()
        if "override" in fname:
            return True
        if fname in ("docker-compose.yaml", "docker-compose.yml"):
            return True
        return False

    def _check_prod_port_exposed(self, data: dict, compose_file: Path) -> list[Finding]:
        """Production compose should not expose host ports (use reverse proxy)."""
        findings: list[Finding] = []

        if self._is_dev_intended_compose(compose_file):
            return findings

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
                        rule="V05-PROD-PORT-EXPOSED",
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

        if self._is_dev_intended_compose(compose_file):
            return findings

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
                            rule="V05-PROD-DEV-MODE",
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
                            rule="V05-PROD-DEV-MODE",
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

        # Skip dev-intended files
        fname = compose_file.name.lower()
        if "override" in fname or fname == "docker-compose.yaml" or fname == "docker-compose.yml":
            return findings

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
                            rule="V05-PROD-WILDCARD-CORS",
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
                            rule="V05-PROD-NO-TRAEFIK-LABELS",
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

        if self._is_dev_intended_compose(compose_file):
            return findings

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
                        rule="V05-PROD-NO-RESOURCE-LIMITS",
                        message=f"Service '{svc_name}' has no resource limits in production",
                        fix=(
                            f"Add deploy.resources.limits (cpus, memory) to "
                            f"'{svc_name}' in {compose_file.name} to prevent resource starvation"
                        ),
                    )
                )

        return findings

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
                        rule="V05-DEV-NO-VOLUME-MOUNT",
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
                            rule="V05-DEV-NO-BUILD-TARGET",
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

    def _load_compose_file(self, compose_file: Path) -> dict:
        """Load and parse a docker-compose YAML file."""
        try:
            return yaml.safe_load(compose_file.read_text()) or {}
        except (yaml.YAMLError, OSError):
            return {}

    def _check_base_image_latest(self, dockerfile: Path) -> list[Finding]:
        """Warn against using :latest tags or no tags (implicit latest)."""
        findings: list[Finding] = []
        try:
            content = dockerfile.read_text()
        except OSError:
            return findings

        lines = content.split("\n")
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line.startswith("FROM "):
                continue

            # Check for :latest tag or no tag (implicit latest)
            from_match = re.match(r"^FROM\s+([^\s]+)(?:\s+AS\s+\S+)?\s*$", line, re.IGNORECASE)
            if from_match:
                image = from_match.group(1)

                # Skip scratch, ARG variables, and multi-stage references
                if image in ("scratch",) or image.startswith("$"):
                    continue

                # Check if image has no tag (implicit latest) or explicit latest
                if ":" not in image or image.endswith(":latest"):
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(dockerfile),
                            rule="V05-BASE-IMAGE-LATEST",
                            message=f"Base image '{image}' uses :latest tag (implicit or explicit)",
                            fix=(
                                f"Pin to specific version instead of :latest in {dockerfile.name} "
                                f"(e.g., node:20-slim, python:3.11-alpine, ubuntu:22.04)"
                            ),
                            line=line_num,
                        )
                    )

        return findings

    def _check_dockerignore_exists(self, dockerfile: Path) -> list[Finding]:
        """Ensure .dockerignore exists when COPY . . is used."""
        findings: list[Finding] = []
        try:
            content = dockerfile.read_text()
        except OSError:
            return findings

        # Check if Dockerfile contains "COPY . ." pattern
        has_copy_all = re.search(r"^COPY\s+\.\s+\.", content, re.MULTILINE)
        if has_copy_all:
            dockerignore_path = dockerfile.parent / ".dockerignore"
            if not dockerignore_path.exists():
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(dockerfile),
                        rule="V05-MISSING-DOCKERIGNORE",
                        message="Dockerfile uses 'COPY . .' but .dockerignore is missing",
                        fix=(
                            f"Create .dockerignore in {dockerfile.parent} to exclude unnecessary files "
                            f"(.git/, node_modules/, *.log, .env, etc.) and reduce build context size"
                        ),
                    )
                )

        return findings

    def _check_build_target_exists(self, compose_files: list[Path]) -> list[Finding]:
        """Validate that docker-compose.yml build.target references exist in Dockerfiles."""
        findings: list[Finding] = []

        for compose_file in compose_files:
            try:
                data = self._load_compose_file(compose_file)
                if not data:
                    continue
            except Exception:
                continue

            for svc_name, svc_def in (data.get("services") or {}).items():
                if not isinstance(svc_def, dict):
                    continue

                build = svc_def.get("build")
                if not isinstance(build, dict):
                    continue

                target = build.get("target")
                if not target:
                    continue

                # Find corresponding Dockerfile
                dockerfile_path = build.get("dockerfile", "Dockerfile")
                context = build.get("context", ".")

                # Resolve context relative to compose file location
                if Path(context).is_absolute():
                    context_path = Path(context)
                else:
                    context_path = compose_file.parent / context

                # Resolve dockerfile path relative to context
                if not Path(dockerfile_path).is_absolute():
                    dockerfile = context_path / dockerfile_path
                else:
                    dockerfile = Path(dockerfile_path)

                if not dockerfile.exists():
                    continue

                # Check if target stage exists in Dockerfile
                try:
                    dockerfile_content = dockerfile.read_text()
                    # Look for "FROM ... AS target_name" pattern
                    stage_pattern = rf"^FROM\s+.*\s+AS\s+{re.escape(target)}\s*$"
                    if not re.search(stage_pattern, dockerfile_content, re.MULTILINE | re.IGNORECASE):
                        findings.append(
                            Finding(
                                severity="error",
                                file=str(compose_file),
                                rule="V05-BUILD-TARGET-MISSING",
                                message=(
                                    f"Service '{svc_name}' references build target '{target}' "
                                    f"which doesn't exist in {dockerfile.name}"
                                ),
                                fix=(
                                    f"Add 'FROM ... AS {target}' stage to {dockerfile.name} "
                                    f"or fix the target name in {compose_file.name}"
                                ),
                            )
                        )
                except OSError:
                    continue

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
    validator = DockerValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
