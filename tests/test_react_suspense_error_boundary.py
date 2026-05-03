"""Tests for V72 — react-suspense-error-boundary (Phase 73)."""

from __future__ import annotations

import pytest

from hooks.validators.react_suspense_error_boundary import (
    ReactSuspenseErrorBoundaryValidator,
)


@pytest.fixture
def validator() -> ReactSuspenseErrorBoundaryValidator:
    return ReactSuspenseErrorBoundaryValidator()


def _write_tsx(tmp_project, name: str, body: str, subdir: str = ""):
    f = tmp_project / "web" / "src" / subdir / name if subdir else tmp_project / "web" / "src" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)
    return f


# ── 1. Suspense + EB same file → pass ────────────────────────────────────────


class TestSameFilePairingPasses:
    def test_eb_in_same_file_passes(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Page.tsx",
            "import { Suspense } from 'react';\n"
            "import { ErrorBoundary } from 'react-error-boundary';\n"
            "export default function Page() {\n"
            "  return (\n"
            "    <ErrorBoundary fallback={<div>err</div>}>\n"
            "      <Suspense fallback={<div>load</div>}><Data/></Suspense>\n"
            "    </ErrorBoundary>\n"
            "  );\n"
            "}\n",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. Suspense without EB anywhere → V72-SUSPENSE-NO-EB ─────────────────────


class TestSuspenseAloneFlagged:
    def test_suspense_alone_flagged(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Page.tsx",
            "import { Suspense } from 'react';\n"
            "export default function Page() {\n"
            "  return <Suspense fallback={<div>load</div>}><Data/></Suspense>;\n"
            "}\n",
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V72-SUSPENSE-NO-EB"
        assert findings[0].severity == "warning"


# ── 3. EB in layout file → all Suspense uses pass ────────────────────────────


class TestLayoutEbCovers:
    def test_app_layout_eb_covers_other_files(self, validator, tmp_project, project_ctx):
        # Layout has ErrorBoundary
        layout = tmp_project / "web" / "src" / "app" / "layout.tsx"
        layout.parent.mkdir(parents=True, exist_ok=True)
        layout.write_text(
            "import { ErrorBoundary } from 'react-error-boundary';\n"
            "export default function RootLayout({ children }) {\n"
            "  return <ErrorBoundary fallback={<div>err</div>}>{children}</ErrorBoundary>;\n"
            "}\n"
        )
        # Page only has Suspense, no EB — but layout covers it
        _write_tsx(
            tmp_project,
            "Page.tsx",
            "import { Suspense } from 'react';\n"
            "export default function Page() {\n"
            "  return <Suspense fallback={<div>load</div>}><Data/></Suspense>;\n"
            "}\n",
            subdir="app",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_underscore_app_eb_covers(self, validator, tmp_project, project_ctx):
        # Pages-router style _app.tsx
        app = tmp_project / "web" / "src" / "_app.tsx"
        app.parent.mkdir(parents=True, exist_ok=True)
        app.write_text(
            "import { ErrorBoundary } from 'react-error-boundary';\n"
            "export default function App({ Component, pageProps }) {\n"
            "  return <ErrorBoundary fallback={null}><Component {...pageProps}/></ErrorBoundary>;\n"
            "}\n"
        )
        _write_tsx(
            tmp_project,
            "Index.tsx",
            "import { Suspense } from 'react';\n"
            "export default function Index() { return <Suspense fallback={null}><X/></Suspense>; }\n",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 4. No Suspense → silent ──────────────────────────────────────────────────


class TestNoSuspenseSilent:
    def test_no_suspense_no_findings(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Plain.tsx",
            "export function Plain() { return <div>hi</div>; }\n",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 5. Multiple Suspense in same file — only one finding per file ────────────


class TestMultipleSuspenseSameFile:
    def test_only_first_suspense_flagged(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Many.tsx",
            "import { Suspense } from 'react';\n"
            "export default function Many() {\n"
            "  return (\n"
            "    <>\n"
            "      <Suspense fallback={<div>1</div>}><A/></Suspense>\n"
            "      <Suspense fallback={<div>2</div>}><B/></Suspense>\n"
            "    </>\n"
            "  );\n"
            "}\n",
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1


# ── 6. Escape hatch ──────────────────────────────────────────────────────────


class TestEscapeHatch:
    def test_eb_elsewhere_silences(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Page.tsx",
            "import { Suspense } from 'react';\n"
            "export default function Page() {\n"
            "  // EB lives in a custom upstream wrapper not detected by heuristic\n"
            "  return <Suspense fallback={<div>load</div>}><Data/></Suspense>; // verifier:suspense-eb-elsewhere upstream\n"
            "}\n",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 7. FallbackBoundary alias ────────────────────────────────────────────────


class TestFallbackBoundaryAlias:
    def test_fallback_boundary_recognized(self, validator, tmp_project, project_ctx):
        # Some teams alias ErrorBoundary as FallbackBoundary
        _write_tsx(
            tmp_project,
            "Page.tsx",
            "import { Suspense } from 'react';\n"
            "import { FallbackBoundary } from '@/components/fallback';\n"
            "export default function Page() {\n"
            "  return <FallbackBoundary><Suspense fallback={null}><X/></Suspense></FallbackBoundary>;\n"
            "}\n",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 8. Generated files excluded ──────────────────────────────────────────────


class TestGeneratedFilesExcluded:
    def test_gen_file_skipped(self, validator, tmp_project, project_ctx):
        f = tmp_project / "web" / "src" / "auto.gen.tsx"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "import { Suspense } from 'react';\n"
            "export default function X() { return <Suspense fallback={null}><A/></Suspense>; }\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 9. validate_file (Tier 2) ────────────────────────────────────────────────


class TestValidateFileTier2:
    def test_validate_file_runs_check(self, validator, tmp_project, project_ctx):
        f = _write_tsx(
            tmp_project,
            "X.tsx",
            "import { Suspense } from 'react';\n"
            "export default function X() { return <Suspense fallback={null}><A/></Suspense>; }\n",
        )
        findings = validator.validate_file(project_ctx, str(f))
        assert len(findings) == 1
        assert findings[0].rule == "V72-SUSPENSE-NO-EB"
