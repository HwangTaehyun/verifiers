"""Tests for V02: GraphQL/genqlient validator (graphql_gen.py).

Covers:
  - _check_genqlient_yaml: missing file, missing fields, all fields present
  - _check_omitempty: detect missing omitempty on *uuid.UUID, pass when present
  - _check_stale_generated: stale detection via mtime, up-to-date (no finding)
  - _check_function_references: missing function in generated code, all present
"""

from __future__ import annotations

import os
import time
from pathlib import Path


from hooks.validators.graphql_gen import GraphqlGenValidator
from lib.project_context import ProjectContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GENQLIENT_YAML_FULL = """\
schema:
  - schemas/*.graphql
operations:
  - queries/**/*.graphql
generated: gqlclient/genqlient.go
package: gqlclient
"""

GENQLIENT_YAML_MISSING_SCHEMA = """\
operations:
  - queries/**/*.graphql
generated: gqlclient/genqlient.go
package: gqlclient
"""

GENQLIENT_YAML_MISSING_MULTIPLE = """\
schema:
  - schemas/*.graphql
"""

GENERATED_GO_WITH_OMITEMPTY_MISSING = """\
package gqlclient

import "github.com/google/uuid"

type CreateFooInput struct {
\tFooID  *uuid.UUID `json:"fooId"`
\tBarID  *uuid.UUID `json:"barId,omitempty"`
\tName   string     `json:"name"`
}

func CreateFoo(ctx context.Context, client graphql.Client) (*CreateFooResponse, error) {
\treturn nil, nil
}

func GetBar(ctx context.Context, client graphql.Client) (*GetBarResponse, error) {
\treturn nil, nil
}
"""

GENERATED_GO_ALL_OMITEMPTY = """\
package gqlclient

import "github.com/google/uuid"

type CreateFooInput struct {
\tFooID  *uuid.UUID `json:"fooId,omitempty"`
\tBarID  *uuid.UUID `json:"barId,omitempty"`
\tName   string     `json:"name"`
}

func CreateFoo(ctx context.Context, client graphql.Client) (*CreateFooResponse, error) {
\treturn nil, nil
}

func GetBar(ctx context.Context, client graphql.Client) (*GetBarResponse, error) {
\treturn nil, nil
}
"""

REPOSITORY_GO_WITH_MISSING_FUNC = """\
package repository

import "myproject/server/graph/gqlclient"

func (r *Repo) DoSomething(ctx context.Context) error {
\tresp, err := gqlclient.CreateFoo(ctx, r.client)
\tif err != nil {
\t\treturn err
\t}
\tresp2, err := gqlclient.NonExistentQuery(ctx, r.client)
\tif err != nil {
\t\treturn err
\t}
\treturn nil
}
"""

REPOSITORY_GO_ALL_PRESENT = """\
package repository

import "myproject/server/graph/gqlclient"

func (r *Repo) DoSomething(ctx context.Context) error {
\tresp, err := gqlclient.CreateFoo(ctx, r.client)
\tif err != nil {
\t\treturn err
\t}
\tresp2, err := gqlclient.GetBar(ctx, r.client)
\tif err != nil {
\t\treturn err
\t}
\treturn nil
}
"""

SIMPLE_GRAPHQL_SCHEMA = """\
type Query {
  foo(id: ID!): Foo
}

type Foo {
  id: ID!
  name: String!
}
"""

SIMPLE_GRAPHQL_QUERY = """\
query CreateFoo($input: CreateFooInput!) {
  createFoo(input: $input) {
    id
    name
  }
}
"""


# ---------------------------------------------------------------------------
# Tests: _check_genqlient_yaml
# ---------------------------------------------------------------------------


class TestCheckGenqlientYaml:
    """Tests for _check_genqlient_yaml."""

    def test_missing_yaml_file(self, tmp_project: Path, project_ctx: ProjectContext):
        """When genqlient.yaml does not exist, produce a YAML-MISSING-FIELD finding."""
        validator = GraphqlGenValidator()

        # Ensure no genqlient.yaml exists (tmp_project fixture doesn't create one)
        yaml_path = tmp_project / "server" / "graph" / "genqlient.yaml"
        assert not yaml_path.exists()

        findings = validator._check_genqlient_yaml(project_ctx)

        assert len(findings) == 1
        assert findings[0].rule == "V02-YAML-MISSING-FIELD"
        assert findings[0].severity == "error"
        assert "genqlient.yaml not found" in findings[0].message

    def test_missing_required_fields(self, tmp_project: Path, project_ctx: ProjectContext):
        """When genqlient.yaml is missing some required fields, report each one."""
        yaml_path = tmp_project / "server" / "graph" / "genqlient.yaml"
        yaml_path.write_text(GENQLIENT_YAML_MISSING_SCHEMA)

        validator = GraphqlGenValidator()
        findings = validator._check_genqlient_yaml(project_ctx)

        assert len(findings) == 1
        assert findings[0].rule == "V02-YAML-MISSING-FIELD"
        assert "'schema'" in findings[0].message

    def test_missing_multiple_required_fields(self, tmp_project: Path, project_ctx: ProjectContext):
        """When genqlient.yaml is missing multiple required fields, report all of them."""
        yaml_path = tmp_project / "server" / "graph" / "genqlient.yaml"
        yaml_path.write_text(GENQLIENT_YAML_MISSING_MULTIPLE)

        validator = GraphqlGenValidator()
        findings = validator._check_genqlient_yaml(project_ctx)

        # Missing: operations, generated, package
        assert len(findings) == 3
        rules = {f.rule for f in findings}
        assert rules == {"V02-YAML-MISSING-FIELD"}

        missing_fields = {f.message for f in findings}
        assert any("'operations'" in m for m in missing_fields)
        assert any("'generated'" in m for m in missing_fields)
        assert any("'package'" in m for m in missing_fields)

    def test_all_fields_present(self, tmp_project: Path, project_ctx: ProjectContext):
        """When genqlient.yaml has all required fields, produce no findings."""
        yaml_path = tmp_project / "server" / "graph" / "genqlient.yaml"
        yaml_path.write_text(GENQLIENT_YAML_FULL)

        validator = GraphqlGenValidator()
        findings = validator._check_genqlient_yaml(project_ctx)

        assert len(findings) == 0


# ---------------------------------------------------------------------------
# Tests: _check_omitempty
# ---------------------------------------------------------------------------


class TestCheckOmitempty:
    """Tests for _check_omitempty."""

    def test_detect_missing_omitempty(self, tmp_project: Path, project_ctx: ProjectContext):
        """*uuid.UUID field with json tag missing omitempty should produce a finding."""
        gen_file = tmp_project / "server" / "graph" / "gqlclient" / "genqlient.go"
        gen_file.write_text(GENERATED_GO_WITH_OMITEMPTY_MISSING)

        validator = GraphqlGenValidator()
        findings = validator._check_omitempty(project_ctx)

        assert len(findings) == 1
        assert findings[0].rule == "V02-OMITEMPTY"
        assert findings[0].severity == "error"
        assert "FooID" in findings[0].message
        assert "omitempty" in findings[0].fix
        assert findings[0].line is not None

    def test_pass_when_omitempty_present(self, tmp_project: Path, project_ctx: ProjectContext):
        """When all *uuid.UUID fields have omitempty, produce no findings."""
        gen_file = tmp_project / "server" / "graph" / "gqlclient" / "genqlient.go"
        gen_file.write_text(GENERATED_GO_ALL_OMITEMPTY)

        validator = GraphqlGenValidator()
        findings = validator._check_omitempty(project_ctx)

        assert len(findings) == 0

    def test_no_generated_file(self, tmp_project: Path, project_ctx: ProjectContext):
        """When genqlient.go does not exist, produce no findings (graceful skip)."""
        gen_file = tmp_project / "server" / "graph" / "gqlclient" / "genqlient.go"
        assert not gen_file.exists()

        validator = GraphqlGenValidator()
        findings = validator._check_omitempty(project_ctx)

        assert len(findings) == 0


# ---------------------------------------------------------------------------
# Tests: _check_stale_generated
# ---------------------------------------------------------------------------


class TestCheckStaleGenerated:
    """Tests for _check_stale_generated."""

    def test_stale_when_input_newer(self, tmp_project: Path, project_ctx: ProjectContext):
        """When input files are newer than generated file, produce a stale finding."""
        graph_dir = tmp_project / "server" / "graph"

        # Create genqlient.yaml
        yaml_path = graph_dir / "genqlient.yaml"
        yaml_path.write_text(GENQLIENT_YAML_FULL)

        # Create generated file FIRST (older)
        gen_file = graph_dir / "gqlclient" / "genqlient.go"
        gen_file.write_text(GENERATED_GO_ALL_OMITEMPTY)

        # Set generated file mtime to the past
        old_time = time.time() - 3600  # 1 hour ago
        os.utime(gen_file, (old_time, old_time))

        # Create schema file (newer)
        schema_file = graph_dir / "schemas" / "schema.graphql"
        schema_file.write_text(SIMPLE_GRAPHQL_SCHEMA)

        # Create query file (newer)
        query_file = graph_dir / "queries" / "foo.graphql"
        query_file.write_text(SIMPLE_GRAPHQL_QUERY)

        # The validator uses HashCache. For the stale check to trigger,
        # has_changed must return True. We need to pre-seed the cache so
        # that the hash comparison shows a change.
        validator = GraphqlGenValidator()

        # First call: seeds the cache (returns no change since it's the first time)
        validator._check_stale_generated(project_ctx)  # seeds the cache

        # Now modify an input file to change the hash
        query_file.write_text(SIMPLE_GRAPHQL_QUERY + "\n# modified\n")

        # The mtime of the query file is already newer than gen_file
        # (gen_file was set to 1 hour ago), so as long as has_changed=True,
        # we get the finding.
        findings = validator._check_stale_generated(project_ctx)

        assert len(findings) == 1
        assert findings[0].rule == "V02-STALE-GEN"
        assert findings[0].severity == "error"
        assert "not regenerated" in findings[0].message
        assert "make generate_go" in findings[0].fix

    def test_up_to_date_no_finding(self, tmp_project: Path, project_ctx: ProjectContext):
        """When generated file is newer than all input files, produce no findings."""
        graph_dir = tmp_project / "server" / "graph"

        # Create schema file FIRST (older)
        schema_file = graph_dir / "schemas" / "schema.graphql"
        schema_file.write_text(SIMPLE_GRAPHQL_SCHEMA)

        # Create query file (older)
        query_file = graph_dir / "queries" / "foo.graphql"
        query_file.write_text(SIMPLE_GRAPHQL_QUERY)

        # Create genqlient.yaml (older)
        yaml_path = graph_dir / "genqlient.yaml"
        yaml_path.write_text(GENQLIENT_YAML_FULL)

        # Set input files to the past
        old_time = time.time() - 3600
        os.utime(schema_file, (old_time, old_time))
        os.utime(query_file, (old_time, old_time))
        os.utime(yaml_path, (old_time, old_time))

        # Create generated file LAST (newer)
        gen_file = graph_dir / "gqlclient" / "genqlient.go"
        gen_file.write_text(GENERATED_GO_ALL_OMITEMPTY)

        validator = GraphqlGenValidator()
        findings = validator._check_stale_generated(project_ctx)

        # Either has_changed returns False (first time, no cached hash)
        # or mtime comparison shows generated is newer. Either way, no finding.
        assert len(findings) == 0

    def test_no_input_files(self, tmp_project: Path, project_ctx: ProjectContext):
        """When there are no input files at all, produce no findings."""
        # Don't create any .graphql files or genqlient.yaml
        gen_file = tmp_project / "server" / "graph" / "gqlclient" / "genqlient.go"
        gen_file.write_text(GENERATED_GO_ALL_OMITEMPTY)

        validator = GraphqlGenValidator()
        findings = validator._check_stale_generated(project_ctx)

        assert len(findings) == 0

    def test_no_generated_file(self, tmp_project: Path, project_ctx: ProjectContext):
        """When genqlient.go does not exist, produce no findings (graceful skip)."""
        graph_dir = tmp_project / "server" / "graph"

        # Create input files but no generated file
        schema_file = graph_dir / "schemas" / "schema.graphql"
        schema_file.write_text(SIMPLE_GRAPHQL_SCHEMA)

        yaml_path = graph_dir / "genqlient.yaml"
        yaml_path.write_text(GENQLIENT_YAML_FULL)

        validator = GraphqlGenValidator()
        findings = validator._check_stale_generated(project_ctx)

        assert len(findings) == 0


# ---------------------------------------------------------------------------
# Tests: _check_function_references
# ---------------------------------------------------------------------------


class TestCheckFunctionReferences:
    """Tests for _check_function_references."""

    def test_missing_function_in_generated(self, tmp_project: Path, project_ctx: ProjectContext):
        """Repository calls gqlclient.NonExistentQuery() but it is not in generated code."""
        graph_dir = tmp_project / "server" / "graph"
        server_dir = tmp_project / "server"

        # Create generated file with CreateFoo and GetBar
        gen_file = graph_dir / "gqlclient" / "genqlient.go"
        gen_file.write_text(GENERATED_GO_ALL_OMITEMPTY)

        # Create repository file that calls NonExistentQuery (not in generated code)
        internal_dir = server_dir / "internal"
        repo_file = internal_dir / "repository.go"
        repo_file.write_text(REPOSITORY_GO_WITH_MISSING_FUNC)

        validator = GraphqlGenValidator()
        findings = validator._check_function_references(project_ctx)

        assert len(findings) == 1
        assert findings[0].rule == "V02-MISSING-FUNCTION"
        assert findings[0].severity == "error"
        assert "NonExistentQuery" in findings[0].message
        assert findings[0].line is not None

    def test_all_referenced_functions_exist(self, tmp_project: Path, project_ctx: ProjectContext):
        """All gqlclient.* calls in the repository exist in generated code."""
        graph_dir = tmp_project / "server" / "graph"
        server_dir = tmp_project / "server"

        # Create generated file with CreateFoo and GetBar
        gen_file = graph_dir / "gqlclient" / "genqlient.go"
        gen_file.write_text(GENERATED_GO_ALL_OMITEMPTY)

        # Create repository file that only calls CreateFoo and GetBar
        internal_dir = server_dir / "internal"
        repo_file = internal_dir / "repository.go"
        repo_file.write_text(REPOSITORY_GO_ALL_PRESENT)

        validator = GraphqlGenValidator()
        findings = validator._check_function_references(project_ctx)

        assert len(findings) == 0

    def test_no_generated_file(self, tmp_project: Path, project_ctx: ProjectContext):
        """When genqlient.go does not exist, produce no findings (graceful skip)."""
        validator = GraphqlGenValidator()
        findings = validator._check_function_references(project_ctx)

        assert len(findings) == 0

    def test_no_repository_files(self, tmp_project: Path, project_ctx: ProjectContext):
        """When no repository*.go files exist, produce no findings."""
        gen_file = tmp_project / "server" / "graph" / "gqlclient" / "genqlient.go"
        gen_file.write_text(GENERATED_GO_ALL_OMITEMPTY)

        validator = GraphqlGenValidator()
        findings = validator._check_function_references(project_ctx)

        assert len(findings) == 0


# ---------------------------------------------------------------------------
# Tests: Full validate() method integration
# ---------------------------------------------------------------------------


class TestValidateIntegration:
    """Integration tests for the full validate() method."""

    def test_no_graph_dir_returns_empty(self, tmp_path: Path):
        """When graph_dir does not exist, validate returns empty findings."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        # No server/graph directory at all
        assert ctx.graph_dir is None

        validator = GraphqlGenValidator()
        result = validator.run(ctx)

        assert len(result.findings) == 0
        assert result.validator_id == "V02-graphql-gen"

    def test_stop_mode_includes_function_references(self, tmp_project: Path, project_ctx: ProjectContext):
        """In stop mode, _check_function_references is always invoked."""
        graph_dir = tmp_project / "server" / "graph"
        server_dir = tmp_project / "server"

        # Create valid yaml
        yaml_path = graph_dir / "genqlient.yaml"
        yaml_path.write_text(GENQLIENT_YAML_FULL)

        # Create generated file
        gen_file = graph_dir / "gqlclient" / "genqlient.go"
        gen_file.write_text(GENERATED_GO_ALL_OMITEMPTY)

        # Create repository file with missing function
        repo_file = server_dir / "internal" / "repository.go"
        repo_file.write_text(REPOSITORY_GO_WITH_MISSING_FUNC)

        validator = GraphqlGenValidator()
        result = validator.run(project_ctx, mode="stop")

        # Should contain the V02-MISSING-FUNCTION finding
        rules = {f.rule for f in result.findings}
        assert "V02-MISSING-FUNCTION" in rules

    def test_post_tool_use_mode_skips_function_references_for_unrelated_file(
        self, tmp_project: Path, project_ctx: ProjectContext
    ):
        """In post_tool_use mode with an unrelated file, _check_function_references is skipped."""
        graph_dir = tmp_project / "server" / "graph"
        server_dir = tmp_project / "server"

        # Create valid yaml
        yaml_path = graph_dir / "genqlient.yaml"
        yaml_path.write_text(GENQLIENT_YAML_FULL)

        # Create generated file
        gen_file = graph_dir / "gqlclient" / "genqlient.go"
        gen_file.write_text(GENERATED_GO_ALL_OMITEMPTY)

        # Create repository file with missing function
        repo_file = server_dir / "internal" / "repository.go"
        repo_file.write_text(REPOSITORY_GO_WITH_MISSING_FUNC)

        validator = GraphqlGenValidator()
        # file_path does not contain "genqlient", mode is not "stop"
        result = validator.run(project_ctx, file_path="server/internal/repository.go", mode="post_tool_use")

        # Should NOT contain V02-MISSING-FUNCTION (function refs check is skipped)
        rules = {f.rule for f in result.findings}
        assert "V02-MISSING-FUNCTION" not in rules

    def test_post_tool_use_mode_includes_function_references_for_genqlient_file(
        self, tmp_project: Path, project_ctx: ProjectContext
    ):
        """In post_tool_use mode with a genqlient-related file, function refs are checked."""
        graph_dir = tmp_project / "server" / "graph"
        server_dir = tmp_project / "server"

        # Create valid yaml
        yaml_path = graph_dir / "genqlient.yaml"
        yaml_path.write_text(GENQLIENT_YAML_FULL)

        # Create generated file
        gen_file = graph_dir / "gqlclient" / "genqlient.go"
        gen_file.write_text(GENERATED_GO_ALL_OMITEMPTY)

        # Create repository file with missing function
        repo_file = server_dir / "internal" / "repository.go"
        repo_file.write_text(REPOSITORY_GO_WITH_MISSING_FUNC)

        validator = GraphqlGenValidator()
        result = validator.run(
            project_ctx,
            file_path="server/graph/gqlclient/genqlient.go",
            mode="post_tool_use",
        )

        # Should contain V02-MISSING-FUNCTION because file_path contains "genqlient"
        rules = {f.rule for f in result.findings}
        assert "V02-MISSING-FUNCTION" in rules
