"""Tests for V76 — rhf-zod-schema-sync (Phase 72)."""

from __future__ import annotations

import pytest

from hooks.validators.rhf_zod_schema_sync import RhfZodSchemaSyncValidator


@pytest.fixture
def validator() -> RhfZodSchemaSyncValidator:
    return RhfZodSchemaSyncValidator()


def _write_tsx(tmp_project, name: str, body: str):
    f = tmp_project / "web" / "src" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)
    return f


# ── 1. Canonical (T = z.infer<typeof S>, useForm<T>(zodResolver(S))) passes ──


class TestCanonicalPasses:
    def test_t_inferred_from_same_schema_passes(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "UserForm.tsx",
            (
                "import { z } from 'zod';\n"
                "import { useForm } from 'react-hook-form';\n"
                "import { zodResolver } from '@hookform/resolvers/zod';\n"
                "\n"
                "const userSchema = z.object({ email: z.string() });\n"
                "type FormData = z.infer<typeof userSchema>;\n"
                "\n"
                "export function UserForm() {\n"
                "  const form = useForm<FormData>({ resolver: zodResolver(userSchema) });\n"
                "  return null;\n"
                "}\n"
            ),
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. Type defined from different schema → V76-RHF-SCHEMA-MISMATCH ──────────


class TestSchemaMismatchFlagged:
    def test_different_schema_flagged(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Bad.tsx",
            (
                "import { z } from 'zod';\n"
                "import { useForm } from 'react-hook-form';\n"
                "import { zodResolver } from '@hookform/resolvers/zod';\n"
                "\n"
                "const userSchema = z.object({ email: z.string() });\n"
                "const adminSchema = z.object({ adminId: z.string() });\n"
                "type FormData = z.infer<typeof userSchema>;\n"
                "\n"
                "export function Bad() {\n"
                "  const form = useForm<FormData>({ resolver: zodResolver(adminSchema) });\n"
                "  return null;\n"
                "}\n"
            ),
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V76-RHF-SCHEMA-MISMATCH"
        assert "userSchema" in findings[0].message
        assert "adminSchema" in findings[0].message


# ── 3. Plain type literal → V76-RHF-NOT-FROM-INFER ───────────────────────────


class TestNotFromInferFlagged:
    def test_plain_type_literal_flagged(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Bad.tsx",
            (
                "import { z } from 'zod';\n"
                "import { useForm } from 'react-hook-form';\n"
                "import { zodResolver } from '@hookform/resolvers/zod';\n"
                "\n"
                "const userSchema = z.object({ email: z.string(), name: z.string() });\n"
                "type FormData = { email: string; phone: string };\n"
                "\n"
                "export function Bad() {\n"
                "  const form = useForm<FormData>({ resolver: zodResolver(userSchema) });\n"
                "  return null;\n"
                "}\n"
            ),
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V76-RHF-NOT-FROM-INFER"

    def test_interface_form_flagged(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "BadIface.tsx",
            (
                "import { z } from 'zod';\n"
                "import { useForm } from 'react-hook-form';\n"
                "import { zodResolver } from '@hookform/resolvers/zod';\n"
                "\n"
                "const userSchema = z.object({ email: z.string() });\n"
                "interface FormData { email: string }\n"
                "\n"
                "export function BadIface() {\n"
                "  const form = useForm<FormData>({ resolver: zodResolver(userSchema) });\n"
                "  return null;\n"
                "}\n"
            ),
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V76-RHF-NOT-FROM-INFER"


# ── 4. T imported (not defined in file) → silent ─────────────────────────────


class TestImportedTypeSilent:
    def test_imported_t_no_finding(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Form.tsx",
            (
                "import { z } from 'zod';\n"
                "import { useForm } from 'react-hook-form';\n"
                "import { zodResolver } from '@hookform/resolvers/zod';\n"
                "import type { FormData } from './types';\n"
                "\n"
                "const userSchema = z.object({ email: z.string() });\n"
                "\n"
                "export function Form() {\n"
                "  const form = useForm<FormData>({ resolver: zodResolver(userSchema) });\n"
                "  return null;\n"
                "}\n"
            ),
        )
        findings = validator.validate_project(project_ctx)
        # FormData not defined in this file — silent (cross-file out of v1 scope)
        assert findings == []


# ── 5. Non-RHF / non-Zod file is no-op ───────────────────────────────────────


class TestNonRhfFileNoop:
    def test_no_useform_silent(self, validator, tmp_project, project_ctx):
        _write_tsx(tmp_project, "plain.tsx", "export const x = 1;\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_useform_without_zodresolver_silent(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Form.tsx",
            (
                "import { useForm } from 'react-hook-form';\n"
                "type FormData = { email: string };\n"
                "export function Form() {\n"
                "  const form = useForm<FormData>({ defaultValues: { email: '' } });\n"
                "  return null;\n"
                "}\n"
            ),
        )
        findings = validator.validate_project(project_ctx)
        # No zodResolver call — V76 doesn't apply
        assert findings == []


# ── 6. Generated files excluded ──────────────────────────────────────────────


class TestGeneratedFilesExcluded:
    def test_gen_file_skipped(self, validator, tmp_project, project_ctx):
        f = tmp_project / "web" / "src" / "schema.gen.tsx"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "import { z } from 'zod';\n"
            "import { useForm } from 'react-hook-form';\n"
            "import { zodResolver } from '@hookform/resolvers/zod';\n"
            "const s = z.object({}); type T = { x: string };\n"
            "useForm<T>({ resolver: zodResolver(s) });\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 7. validate_file (Tier 2) ────────────────────────────────────────────────


class TestValidateFileTier2:
    def test_validate_file_only_target(self, validator, tmp_project, project_ctx):
        f1 = _write_tsx(
            tmp_project,
            "A.tsx",
            (
                "import { z } from 'zod';\n"
                "import { useForm } from 'react-hook-form';\n"
                "import { zodResolver } from '@hookform/resolvers/zod';\n"
                "const s = z.object({}); type T = { x: string };\n"
                "useForm<T>({ resolver: zodResolver(s) });\n"
            ),
        )
        f2 = _write_tsx(
            tmp_project,
            "B.tsx",
            (
                "import { z } from 'zod';\n"
                "import { useForm } from 'react-hook-form';\n"
                "import { zodResolver } from '@hookform/resolvers/zod';\n"
                "const s = z.object({}); type T = { x: string };\n"
                "useForm<T>({ resolver: zodResolver(s) });\n"
            ),
        )
        findings = validator.validate_file(project_ctx, str(f1))
        assert len(findings) == 1
        assert findings[0].file == str(f1)
        assert all(f2 != x.file for x in findings)


# ── 8. Multiple useForm in same file ─────────────────────────────────────────


class TestMultipleUseFormInSameFile:
    def test_independent_validation(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Many.tsx",
            (
                "import { z } from 'zod';\n"
                "import { useForm } from 'react-hook-form';\n"
                "import { zodResolver } from '@hookform/resolvers/zod';\n"
                "\n"
                "const aSchema = z.object({ a: z.string() });\n"
                "const bSchema = z.object({ b: z.string() });\n"
                "type AData = z.infer<typeof aSchema>;     // ✓ correct\n"
                "type BData = { b: string };                // ✗ literal\n"
                "\n"
                "function A() { useForm<AData>({ resolver: zodResolver(aSchema) }); }\n"
                "function B() { useForm<BData>({ resolver: zodResolver(bSchema) }); }\n"
            ),
        )
        findings = validator.validate_project(project_ctx)
        # A is correct, B is V76-RHF-NOT-FROM-INFER
        assert len(findings) == 1
        assert findings[0].rule == "V76-RHF-NOT-FROM-INFER"
        assert "BData" in findings[0].message
