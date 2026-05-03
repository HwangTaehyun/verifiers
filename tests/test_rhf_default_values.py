"""Tests for V77 — rhf-default-values (Phase 73)."""

from __future__ import annotations

import pytest

from hooks.validators.rhf_default_values import RhfDefaultValuesValidator


@pytest.fixture
def validator() -> RhfDefaultValuesValidator:
    return RhfDefaultValuesValidator()


def _write_tsx(tmp_project, name: str, body: str):
    f = tmp_project / "web" / "src" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)
    return f


# ── 1. Complete defaults (all keys covered) → pass ───────────────────────────


class TestCompleteDefaultsPasses:
    def test_zinfer_complete_passes(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Form.tsx",
            "import { z } from 'zod';\n"
            "import { useForm } from 'react-hook-form';\n"
            "const userSchema = z.object({ email: z.string(), name: z.string() });\n"
            "type FormData = z.infer<typeof userSchema>;\n"
            "useForm<FormData>({ defaultValues: { email: '', name: '' } });\n",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_type_literal_complete_passes(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Form.tsx",
            "import { useForm } from 'react-hook-form';\n"
            "type FormData = { email: string; age: number };\n"
            "useForm<FormData>({ defaultValues: { email: '', age: 0 } });\n",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_interface_complete_passes(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Form.tsx",
            "import { useForm } from 'react-hook-form';\n"
            "interface FormData { email: string; phone: string }\n"
            "useForm<FormData>({ defaultValues: { email: '', phone: '' } });\n",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. Missing keys → V77-RHF-DEFAULTS-INCOMPLETE ────────────────────────────


class TestMissingKeysFlagged:
    def test_missing_one_key_zinfer(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Bad.tsx",
            "import { z } from 'zod';\n"
            "import { useForm } from 'react-hook-form';\n"
            "const userSchema = z.object({ email: z.string(), name: z.string(), age: z.number() });\n"
            "type FormData = z.infer<typeof userSchema>;\n"
            "useForm<FormData>({ defaultValues: { email: '', name: '' } });\n",
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V77-RHF-DEFAULTS-INCOMPLETE"
        assert findings[0].severity == "error"
        assert "age" in findings[0].message

    def test_missing_multiple_keys_type_literal(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Bad.tsx",
            "import { useForm } from 'react-hook-form';\n"
            "type FormData = { email: string; name: string; age: number };\n"
            "useForm<FormData>({ defaultValues: { email: '' } });\n",
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        msg = findings[0].message
        assert "name" in msg and "age" in msg

    def test_optional_field_still_required_in_defaults(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Bad.tsx",
            "import { useForm } from 'react-hook-form';\n"
            "type FormData = { email: string; phone?: string };\n"
            "useForm<FormData>({ defaultValues: { email: '' } });\n",
        )
        findings = validator.validate_project(project_ctx)
        # Optional in TS but RHF still wants the key for controlled-input behavior.
        assert len(findings) == 1
        assert "phone" in findings[0].message


# ── 3. T imported (not in this file) → silent ───────────────────────────────


class TestImportedTypeSilent:
    def test_imported_t_silent(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Form.tsx",
            "import type { FormData } from './types';\n"
            "import { useForm } from 'react-hook-form';\n"
            "useForm<FormData>({ defaultValues: { email: '' } });\n",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 4. No useForm or no defaultValues → silent ───────────────────────────────


class TestNoUseFormSilent:
    def test_no_useform_silent(self, validator, tmp_project, project_ctx):
        _write_tsx(tmp_project, "plain.tsx", "export const x = 1;\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_useform_no_default_values_silent(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Form.tsx",
            "import { useForm } from 'react-hook-form';\n"
            "type FormData = { email: string };\n"
            "useForm<FormData>();\n",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 5. Extra keys in defaultValues (not in T) → silent ───────────────────────


class TestExtraKeysSilent:
    def test_extra_keys_in_defaults_silent(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Form.tsx",
            "import { useForm } from 'react-hook-form';\n"
            "type FormData = { email: string };\n"
            "useForm<FormData>({ defaultValues: { email: '', extra: '' } });\n",
        )
        findings = validator.validate_project(project_ctx)
        # RHF tolerates extra defaultValues keys (TS will catch real errors)
        assert findings == []


# ── 6. Generated files excluded ──────────────────────────────────────────────


class TestGeneratedFilesExcluded:
    def test_gen_file_skipped(self, validator, tmp_project, project_ctx):
        f = tmp_project / "web" / "src" / "auto.gen.tsx"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "import { useForm } from 'react-hook-form';\n"
            "type FormData = { email: string; name: string };\n"
            "useForm<FormData>({ defaultValues: { email: '' } });\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 7. Multiple useForm in same file ─────────────────────────────────────────


class TestMultipleUseForm:
    def test_each_independently_validated(self, validator, tmp_project, project_ctx):
        _write_tsx(
            tmp_project,
            "Two.tsx",
            "import { useForm } from 'react-hook-form';\n"
            "type AData = { a: string; b: string };\n"
            "type BData = { c: string };\n"
            "function A() { useForm<AData>({ defaultValues: { a: '' } }); }\n"
            "function B() { useForm<BData>({ defaultValues: { c: '' } }); }\n",
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert "AData" in findings[0].message
        assert "b" in findings[0].message


# ── 8. validate_file (Tier 2) ────────────────────────────────────────────────


class TestValidateFileTier2:
    def test_validate_file_only_target(self, validator, tmp_project, project_ctx):
        f = _write_tsx(
            tmp_project,
            "A.tsx",
            "import { useForm } from 'react-hook-form';\n"
            "type FormData = { a: string; b: string };\n"
            "useForm<FormData>({ defaultValues: { a: '' } });\n",
        )
        findings = validator.validate_file(project_ctx, str(f))
        assert len(findings) == 1
        assert findings[0].rule == "V77-RHF-DEFAULTS-INCOMPLETE"
