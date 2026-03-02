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


class DockerComposeValidator(BaseValidator):
    """V05: Docker Compose Validator."""

    id = "V05-docker-compose"
    name = "Docker Compose Validator"
    file_patterns: list[str] = [
        "**/docker-compose*.yaml",
        "**/docker-compose*.yml",
        "**/Dockerfile*",
    ]

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []

        # Find all compose files
        compose_files = list(ctx.project_root.glob("**/docker-compose*.yaml"))
        compose_files.extend(ctx.project_root.glob("**/docker-compose*.yml"))

        # Exclude vendor/node_modules
        compose_files = [f for f in compose_files if "vendor" not in str(f) and "node_modules" not in str(f)]

        if not compose_files:
            return ValidationResult(validator_id=self.id, findings=findings)

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

        return ValidationResult(validator_id=self.id, findings=findings)

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
    validator = DockerComposeValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
