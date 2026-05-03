"""Tests for V66 — ts-no-direct-fetch (Phase 73)."""

from __future__ import annotations

import pytest

from hooks.validators.ts_no_direct_fetch import TsNoDirectFetchValidator


@pytest.fixture
def validator() -> TsNoDirectFetchValidator:
    return TsNoDirectFetchValidator()


def _write_tsx(tmp_project, name: str, body: str, subdir: str = "components"):
    f = tmp_project / "web" / "src" / subdir / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)
    return f


# ── 1. Server Component (no use client, no client hooks) → silent ────────────


class TestServerComponentExempt:
    def test_use_server_directive_exempt(self, validator, tmp_project, project_ctx):
        f = tmp_project / "web" / "src" / "app" / "page.tsx"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "'use server';\n"
            "export default async function Page() {\n"
            "  const data = await fetch('/api/users');\n"
            "  return null;\n"
            "}\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_app_page_no_directive_no_hooks_silent(self, validator, tmp_project, project_ctx):
        # No 'use client', no client hooks, not in /components/ — treated as RSC
        f = tmp_project / "web" / "src" / "app" / "page.tsx"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "export default async function Page() {\n"
            "  const data = await fetch('/api/users');\n"
            "  return null;\n"
            "}\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. Client Component with raw fetch → V66-COMPONENT-DIRECT-FETCH ──────────


class TestClientComponentRawFetchFlagged:
    def test_use_client_directive_fetch_flagged(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "UserCard.tsx",
            "'use client';\n"
            "import { useEffect, useState } from 'react';\n"
            "export function UserCard({ id }) {\n"
            "  const [u, setU] = useState();\n"
            "  useEffect(() => { fetch(`/api/users/${id}`).then(r => r.json()).then(setU); }, [id]);\n"
            "  return null;\n"
            "}\n",
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V66-COMPONENT-DIRECT-FETCH"
        assert findings[0].severity == "warning"

    def test_in_components_dir_fetch_flagged(self, validator, tmp_project, project_ctx):
        # No 'use client' but path matches /components/ → treated as Client
        _write_tsx(
            tmp_project,
            "Plain.tsx",
            "export function Plain() {\n"
            "  fetch('/api/x');\n"
            "  return null;\n"
            "}\n",
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V66-COMPONENT-DIRECT-FETCH"

    def test_axios_get_flagged(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Form.tsx",
            "'use client';\n"
            "import { useState } from 'react';\n"
            "import axios from 'axios';\n"
            "export function Form() {\n"
            "  const [d, setD] = useState();\n"
            "  axios.get('/api/x').then(r => setD(r.data));\n"
            "  return null;\n"
            "}\n",
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert "axios.get" in findings[0].message

    def test_axios_post_also_flagged(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Submit.tsx",
            "'use client';\n"
            "import { useState } from 'react';\n"
            "import axios from 'axios';\n"
            "export function Submit() {\n"
            "  const [v, setV] = useState();\n"
            "  return <button onClick={() => axios.post('/api/x', v)}>x</button>;\n"
            "}\n",
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert "axios.post" in findings[0].message


# ── 3. Service / api / lib paths excluded ────────────────────────────────────


class TestServicePathExcluded:
    def test_service_module_passes(self, validator, tmp_project, project_ctx):
        # services/ is the *correct* place to call fetch — exclude.
        f = tmp_project / "web" / "src" / "services" / "user_service.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "export async function fetchUser(id: string) {\n"
            "  const r = await fetch(`/api/users/${id}`);\n"
            "  return r.json();\n"
            "}\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 4. Escape hatch ──────────────────────────────────────────────────────────


class TestEscapeHatch:
    def test_fetch_ok_silences(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Analytics.tsx",
            "'use client';\n"
            "import { useEffect } from 'react';\n"
            "export function Analytics() {\n"
            "  useEffect(() => {\n"
            "    fetch('/beacon', { method: 'POST' }); // verifier:fetch-ok one-shot beacon\n"
            "  }, []);\n"
            "  return null;\n"
            "}\n",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 5. Generated / build files excluded ──────────────────────────────────────


class TestGeneratedFilesExcluded:
    def test_gen_file_skipped(self, validator, tmp_project, project_ctx):
        f = tmp_project / "web" / "src" / "components" / "Auto.gen.tsx"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "'use client';\n"
            "export function Auto() { fetch('/api/x'); return null; }\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 6. validate_file (Tier 2) ────────────────────────────────────────────────


class TestValidateFileTier2:
    def test_validate_file_only_target(self, validator, tmp_project, project_ctx):
        f = _write_tsx(
            tmp_project,
            "X.tsx",
            "'use client';\n"
            "import { useState } from 'react';\n"
            "export function X() { const [d, sd] = useState(); fetch('/api/x'); return null; }\n",
        )
        findings = validator.validate_file(project_ctx, str(f))
        assert len(findings) == 1
        assert findings[0].rule == "V66-COMPONENT-DIRECT-FETCH"


# ── 7. fetch in non-component utility (no hooks, not in /components/) → silent


class TestNonComponentUtility:
    def test_utility_in_app_dir_silent(self, validator, tmp_project, project_ctx):
        # No 'use client', no hooks, not in /components/ — treated as RSC/server util
        f = tmp_project / "web" / "src" / "utils.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "export async function helper() {\n"
            "  return fetch('/api/x');\n"
            "}\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []
