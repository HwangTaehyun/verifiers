"""V26: Docker compose production hardening.

V05 covers the dev/all-files compose surface. V26 sits on top with
production-only rules that V05 doesn't enforce:

  (a) **`deploy.resources.limits` required.** Every service in a prod
      compose must declare memory + cpu limits. Without them, a
      runaway container can OOM the host or starve siblings.

  (b) **Healthcheck mandatory.** A `depends_on: condition:
      service_healthy` without a corresponding healthcheck blocks
      forever; *missing* depends_on but having a healthcheck is fine,
      but a service the user expects to be observable should have one.

  (c) **`.env` / secret bind-mount banned.** Mounting host `.env` or
      `secrets/*.pem` into a prod container ties image
      reproducibility to per-host state and leaks the security model.
      Use Docker secrets / k8s secrets / env injection instead.

  (d) **`*.localhost` VHOST banned in prod.** RFC 6761 reserves
      ``.localhost`` for loopback resolution. A production compose
      that sets ``VIRTUAL_HOST: api.localhost`` is dev-config that
      slipped through.

V26 fires only on compose files classified as production:
``*.production.*`` / ``*.prod.*`` filename match (configurable via
``docker.production_filename_patterns``).
"""

from __future__ import annotations

import re
import sys
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml  # noqa: E402

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Default filename globs that classify a compose file as "production".
# Can be overridden by ``docker.production_filename_patterns`` config.
_DEFAULT_PROD_PATTERNS: tuple[str, ...] = (
    "*.production.yaml",
    "*.production.yml",
    "*.prod.yaml",
    "*.prod.yml",
    "docker-compose.production.yaml",
    "docker-compose.production.yml",
)

_LOCALHOST_HOST_RE = re.compile(r"\b(?:[\w-]+\.)?localhost\b", re.IGNORECASE)
_VHOST_KEYS = ("VIRTUAL_HOST", "VIRTUAL_HOSTS")


def _is_production_file(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(name, p) for p in patterns)


def _walk_compose(ctx: ProjectContext) -> list[Path]:
    """All compose files in the project (root + server/ + web/)."""
    candidates: list[Path] = []
    for root in (ctx.project_root, ctx.server_dir, ctx.web_dir):
        if root is None:
            continue
        for p in root.glob("docker-compose*.yaml"):
            if p.is_file():
                candidates.append(p)
        for p in root.glob("docker-compose*.yml"):
            if p.is_file():
                candidates.append(p)
    # Dedupe
    seen: set[Path] = set()
    out: list[Path] = []
    for p in candidates:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


class DockerProdHardeningValidator(BaseValidator):
    """V26: Docker compose production hardening."""

    id = "V26-docker-prod"
    name = "Docker Compose Production Hardening"
    file_patterns: list[str] = [
        "**/docker-compose*.yaml",
        "**/docker-compose*.yml",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        path = Path(file_path)
        patterns = self._prod_patterns(ctx)
        if not _is_production_file(path.name, patterns):
            return []
        return self._scan(path)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        patterns = self._prod_patterns(ctx)
        findings: list[Finding] = []
        for compose in _walk_compose(ctx):
            if _is_production_file(compose.name, patterns):
                findings.extend(self._scan(compose))
        return findings

    # ── helpers ──────────────────────────────────────────────────────

    def _prod_patterns(self, ctx: ProjectContext) -> tuple[str, ...]:
        try:
            user = tuple(ctx.config.docker.production_filename_patterns or ())
        except AttributeError:
            user = ()
        return user or _DEFAULT_PROD_PATTERNS

    def _scan(self, compose_file: Path) -> list[Finding]:
        try:
            data = yaml.safe_load(compose_file.read_text(errors="replace")) or {}
        except (yaml.YAMLError, OSError):
            return []
        if not isinstance(data, dict):
            return []
        services = data.get("services") or {}
        if not isinstance(services, dict):
            return []

        findings: list[Finding] = []
        for svc_name, svc in services.items():
            if not isinstance(svc, dict):
                continue
            findings.extend(self._check_resource_limits(compose_file, svc_name, svc))
            findings.extend(self._check_healthcheck(compose_file, svc_name, svc, services))
            findings.extend(self._check_secret_mount(compose_file, svc_name, svc))
            findings.extend(self._check_localhost_vhost(compose_file, svc_name, svc))
        return findings

    # ── (a) deploy.resources.limits ──────────────────────────────────

    def _check_resource_limits(self, compose_file: Path, svc_name: str, svc: dict) -> list[Finding]:
        deploy = svc.get("deploy") or {}
        resources = deploy.get("resources") if isinstance(deploy, dict) else None
        limits = resources.get("limits") if isinstance(resources, dict) else None
        if isinstance(limits, dict) and (limits.get("memory") or limits.get("cpus")):
            return []
        return [
            Finding(
                severity="warning",
                file=str(compose_file),
                rule="V26-PROD-NO-RESOURCE-LIMITS",
                message=(
                    f"Service '{svc_name}' has no deploy.resources.limits. "
                    "A runaway container can OOM the host or starve siblings."
                ),
                fix=('Add `deploy: { resources: { limits: { memory: 512M, cpus: "0.5" } } }` tuned for the workload.'),
            )
        ]

    # ── (b) healthcheck mandatory + depends_on consistency ──────────

    def _check_healthcheck(self, compose_file: Path, svc_name: str, svc: dict, services: dict) -> list[Finding]:
        findings: list[Finding] = []
        # If this service is depended on with `condition: service_healthy`,
        # it MUST declare a healthcheck. (V05 covers the dev/all surface;
        # in prod, V26 is loud about it because failures are silent-block.)
        for other_name, other in services.items():
            if other_name == svc_name or not isinstance(other, dict):
                continue
            depends = other.get("depends_on")
            if not isinstance(depends, dict):
                continue
            entry = depends.get(svc_name)
            if not isinstance(entry, dict):
                continue
            if entry.get("condition") == "service_healthy" and "healthcheck" not in svc:
                findings.append(
                    Finding(
                        severity="error",
                        file=str(compose_file),
                        rule="V26-PROD-NO-HEALTHCHECK",
                        message=(
                            f"Service '{svc_name}' is depended on with "
                            f"`condition: service_healthy` (by '{other_name}') "
                            "but has no `healthcheck:` defined. The dependent "
                            "service will block forever."
                        ),
                        fix=(
                            f"Add a healthcheck block to '{svc_name}': e.g. "
                            '`healthcheck: { test: ["CMD", "curl", "-f", "http://localhost:8080/health"], '
                            "interval: 10s, timeout: 5s, retries: 3 }`"
                        ),
                    )
                )
                break  # one finding per service is enough
        return findings

    # ── (c) .env / secret bind-mount banned ──────────────────────────

    def _check_secret_mount(self, compose_file: Path, svc_name: str, svc: dict) -> list[Finding]:
        volumes = svc.get("volumes")
        if not isinstance(volumes, list):
            return []
        findings: list[Finding] = []
        for vol in volumes:
            host_path = ""
            if isinstance(vol, str):
                host_path = vol.split(":", 1)[0]
            elif isinstance(vol, dict):
                host_path = str(vol.get("source") or "")
            else:
                continue
            host_path_lower = host_path.lower()

            # Bind-mounted secret indicators
            secret_hits = []
            if host_path_lower.endswith(".env") or "/.env" in host_path_lower:
                secret_hits.append(".env")
            if host_path_lower.endswith((".pem", ".key", ".crt", ".p12")):
                secret_hits.append(Path(host_path).suffix)
            if "/secrets" in host_path_lower or host_path_lower.startswith("./secrets"):
                secret_hits.append("secrets/")

            if secret_hits:
                findings.append(
                    Finding(
                        severity="error",
                        file=str(compose_file),
                        rule="V26-PROD-SECRET-BIND-MOUNT",
                        message=(
                            f"Service '{svc_name}' bind-mounts a secret-shaped path "
                            f"({', '.join(secret_hits)}) at '{host_path}'. Production "
                            "must use Docker / k8s secrets, not host bind mounts."
                        ),
                        fix=(
                            "Replace with `environment:` injection from compose env "
                            "vars, or use `secrets:` with `external: true` and a "
                            "secret manager."
                        ),
                    )
                )
        return findings

    # ── (d) *.localhost VHOST banned in prod ─────────────────────────

    def _check_localhost_vhost(self, compose_file: Path, svc_name: str, svc: dict) -> list[Finding]:
        findings: list[Finding] = []
        env = svc.get("environment")
        env_pairs: list[tuple[str, str]] = []
        if isinstance(env, dict):
            env_pairs = [(str(k), str(v)) for k, v in env.items()]
        elif isinstance(env, list):
            for item in env:
                if isinstance(item, str) and "=" in item:
                    k, _, v = item.partition("=")
                    env_pairs.append((k.strip(), v.strip()))

        for key, value in env_pairs:
            if key in _VHOST_KEYS and _LOCALHOST_HOST_RE.search(value):
                findings.append(
                    Finding(
                        severity="error",
                        file=str(compose_file),
                        rule="V26-PROD-LOCALHOST-VHOST",
                        message=(
                            f"Service '{svc_name}' / {key}='{value}' contains a "
                            ".localhost domain in a production compose. RFC 6761 "
                            "reserves .localhost for loopback — production "
                            "traffic will never resolve to this service."
                        ),
                        fix=(
                            "Set the production VIRTUAL_HOST to a real domain, "
                            "e.g. `VIRTUAL_HOST: ${API_DOMAIN}` and provide "
                            "API_DOMAIN in the production .env."
                        ),
                    )
                )

        # Also scan Traefik / similar labels
        labels = svc.get("labels")
        label_strs: list[str] = []
        if isinstance(labels, dict):
            label_strs = [f"{k}={v}" for k, v in labels.items()]
        elif isinstance(labels, list):
            label_strs = [str(item) for item in labels]
        for label in label_strs:
            if "Host(" in label and _LOCALHOST_HOST_RE.search(label):
                findings.append(
                    Finding(
                        severity="error",
                        file=str(compose_file),
                        rule="V26-PROD-LOCALHOST-VHOST",
                        message=(
                            f"Service '{svc_name}' Traefik label routes a .localhost host in production: '{{label}}'."
                        ),
                        fix=(
                            "Replace the .localhost host in the Host(...) rule "
                            "with the production domain (env-templated)."
                        ),
                    )
                )
        return findings


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = DockerProdHardeningValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
