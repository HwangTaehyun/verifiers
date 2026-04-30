# V57 — sbom-ci-step

> **Owner**: `hooks/validators/sbom_ci_step.py`
> **Tier**: 2 (PostToolUse) + 3 (Stop)
> **File patterns**: `.github/workflows/*.yml`, `.github/workflows/*.yaml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V57-NO-SBOM-CI` | warning | No workflow under `.github/workflows/` generates a machine-readable SBOM artifact using a recognised tool. |

## Why this verifier exists

Modern software supply-chain regulation requires a *Software Bill of Materials* — a structured, machine-readable inventory of every dependency that goes into a released artifact. Three concrete pressures make SBOM generation non-optional:

1. **NTIA minimum elements** ([NTIA — Software Bill of Materials](https://www.ntia.gov/page/software-bill-materials) — *published 2021-07-12, retrieved 2026-04-30*). The US National Telecommunications and Information Administration defines the minimum data fields required for a legally acceptable SBOM. Without an automated generator in CI, those fields are never collected consistently.

2. **EU Cyber Resilience Act**. Products placed on the EU market must include an SBOM as part of their technical documentation. CI-level generation is the only reliable way to keep it in sync with every release.

3. **SLSA supply-chain integrity** ([SLSA framework v1.0](https://slsa.dev/) — *v1.0 published 2023-04, retrieved 2026-04-30*). SLSA Level 2 requires provenance attestation; an SBOM artifact is the natural companion that records *what was built*, not just *how*.

### V43 vs V57 — different artifacts, same pipeline

V43 (`ci-image-scanning`) checks that built container images are scanned for *known CVEs*. V57 checks that a *dependency inventory artifact* (CycloneDX JSON, SPDX JSON, etc.) is produced and uploaded. A project can satisfy V43 (Trivy with `format: sarif`) without satisfying V57 (no machine-readable SBOM file is produced or stored). Conversely, a project that generates SBOMs but never scans images fails V43 but passes V57. The same Trivy step *can* satisfy both rules simultaneously — but only when `format: cyclonedx` or `format: spdx-json` is set (the SBOM output modes), not the default `sarif` mode (which is a vulnerability report, not a dependency inventory).

## Design rationale

- **Project-level check, not file-level.** SBOM generation typically lives in a release workflow, not in the PR/push workflow. V57 asks "does *any* workflow produce an SBOM?" rather than "does *this specific file* produce one?". Both `validate_file` and `validate_project` delegate to `_check(ctx)`, which walks all workflow files.
- **Warning, not error.** Many projects do not yet have a release workflow at all. V57 nudges rather than blocks, consistent with the maturity of SBOM adoption across the industry.
- **Emit at the directory level.** When no SBOM is found, the finding points to `.github/workflows/` rather than to a specific file, because the absence spans all files.
- **No `.github/workflows/` → silent pass.** Projects that have no CI at all are not penalised; V57 only applies when CI exists.

## How it checks

Lives in `hooks/validators/sbom_ci_step.py`.

### Top-level: `_check(ctx)`

```python
workflows_dir = Path(ctx.project_root) / ".github" / "workflows"
if not workflows_dir.is_dir():
    return []

for wf_file in sorted(workflows_dir.glob("*.yml")) + sorted(...".yaml"):
    if self._workflow_has_sbom(wf_file):
        return []          # at least one workflow satisfies — pass

return [Finding(severity="warning", rule="V57-NO-SBOM-CI", ...)]
```

### `_workflow_has_sbom(file_path)` — per-file scan

Parses the YAML; iterates every step in every job. A step is considered an SBOM generator if **any** of the following hold:

| Detection method | Trigger |
|---|---|
| `uses:` starts with `anchore/sbom-action` | any version suffix works |
| `uses:` starts with `cyclonedx/gh-gomod-generate-sbom` | any version suffix |
| `uses:` starts with `microsoft/sbom-action` | any version suffix |
| `run:` contains `cyclonedx-gomod` | bare CLI invocation |
| `run:` contains `syft` | with or without args |
| `uses: aquasecurity/trivy-action` **and** `with.format` is `cyclonedx`, `spdx-json`, or `spdx_json` | SBOM-mode Trivy only — `sarif` does **not** qualify |

Invalid YAML is logged as a warning and skipped; it does not crash the validator or suppress the finding from other files.

## Could be more effective

- **SBOM upload verification.** V57 currently only checks that an SBOM is *generated*, not that it is uploaded as a workflow artifact or pushed to an attestation store (e.g., Sigstore Rekor). A stricter rule could verify a subsequent `actions/upload-artifact` step references the SBOM file.
- **Format validation.** Detecting `anchore/sbom-action` is sufficient to flag intent, but the `format:` parameter determines whether the output is CycloneDX or SPDX. A future sub-rule could enforce a specific format mandated by policy.
- **SBOM signing.** SLSA Level 3 requires signed provenance. V57 does not check for `cosign` or `sigstore/cosign-installer` steps that would sign the SBOM.
- **Freshness gating.** A generated SBOM in a release workflow that only triggers on tags could be days old relative to the HEAD of a long-lived release branch. CI-on-every-push SBOM generation would be stricter.
- **Multi-ecosystem tools.** Language-specific SBOM generators (e.g., `jake` for Python, `cdxgen` for polyglot) are not yet detected. The current implementation covers the most common Go + generic tools.

## References

- [NTIA — Software Bill of Materials](https://www.ntia.gov/page/software-bill-materials) — US government SBOM minimum-elements definition — *published 2021-07-12, retrieved 2026-04-30*
- [Anchore sbom-action](https://github.com/anchore/sbom-action) — GitHub Action that wraps Syft to produce CycloneDX / SPDX artifacts — *continuously developed since 2021-09, retrieved 2026-04-30*
- [CycloneDX specification](https://cyclonedx.org/) — OWASP-backed SBOM standard; v1.6 published 2024-01-08 — *v1.6 published 2024-01-08, retrieved 2026-04-30*
- [SLSA framework](https://slsa.dev/) — Supply-chain Levels for Software Artifacts; provenance and integrity standard — *v1.0 published 2023-04, retrieved 2026-04-30*
- [Syft — open-source SBOM generator](https://github.com/anchore/syft) — Anchore's CLI tool underlying `sbom-action`; supports CycloneDX, SPDX, and Syft-native formats — *continuously developed since 2021-01, retrieved 2026-04-30*

## Examples

### Pass — anchore/sbom-action

```yaml
# .github/workflows/release.yml
name: Release

on:
  push:
    tags: ['v*']

jobs:
  sbom:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Generate SBOM
        uses: anchore/sbom-action@v0
        with:
          format: cyclonedx-json
          output-file: sbom.cdx.json

      - uses: actions/upload-artifact@v4
        with:
          name: sbom
          path: sbom.cdx.json
```

### Pass — syft via run command

```yaml
# .github/workflows/ci.yml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install Syft
        run: curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh | sh -s -- -b /usr/local/bin
      - name: Generate SBOM
        run: syft . -o cyclonedx-json=sbom.cdx.json
      - uses: actions/upload-artifact@v4
        with: { name: sbom, path: sbom.cdx.json }
```

### Pass — Trivy in SBOM mode (also satisfies V43 if format is cyclonedx)

```yaml
jobs:
  sbom:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aquasecurity/trivy-action@master
        with:
          scan-type: fs
          format: cyclonedx      # ← SBOM output; "sarif" would NOT satisfy V57
          output: sbom.cdx.json
```

### Fail — Trivy in vulnerability-only mode does not satisfy V57

```yaml
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: aquasecurity/trivy-action@master
        with:
          image-ref: myapp:latest
          format: sarif           # → CVE report only; no SBOM artifact produced
          # → V57-NO-SBOM-CI (warning)
```

### Fail — no SBOM tool anywhere

```yaml
# .github/workflows/ci.yml — build, test, push; no SBOM step
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t myapp:latest .
      - run: docker push myapp:latest
      # → V57-NO-SBOM-CI (warning)
      #   No SBOM generator found in any workflow.
```
