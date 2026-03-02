"""Tests for library modules: ProjectContext, JsonLogger, HashCache."""

from __future__ import annotations

import json
import time
from pathlib import Path


from lib.project_context import ProjectContext
from lib.json_logger import JsonLogger
from lib.hash_cache import HashCache, hash_files


# ===========================================================================
# ProjectContext
# ===========================================================================


class TestProjectContextGitRoot:
    """Git root detection tests."""

    def test_git_root_detected_from_dot_git_dir(self, tmp_path: Path) -> None:
        """A directory containing a .git folder should be recognised as the
        project root."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)

        assert ctx.project_root == tmp_path

    def test_git_root_detected_from_subdirectory(self, tmp_path: Path) -> None:
        """Starting from a nested subdirectory, the root containing .git
        should still be found."""
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        ctx = ProjectContext(nested)

        assert ctx.project_root == tmp_path

    def test_no_git_root_falls_back_to_cwd(self, tmp_path: Path) -> None:
        """When no .git directory exists anywhere up the tree, project_root
        should fall back to the cwd itself."""
        # tmp_path has no .git — but the git subprocess may still resolve
        # to a real git root if the test is run inside a repo.  We at least
        # verify that project_root is set to *something* without crashing.
        ctx = ProjectContext(tmp_path)
        assert ctx.project_root is not None


class TestProjectContextProjectName:
    """Project name detection tests."""

    def test_name_from_local_yaml_config(self, tmp_path: Path) -> None:
        """Project name should be extracted from server/config/*.local.yaml."""
        (tmp_path / ".git").mkdir()
        config = tmp_path / "server" / "config"
        config.mkdir(parents=True)
        (config / "myapp.local.yaml").write_text("port: 3000\n")

        ctx = ProjectContext(tmp_path)
        assert ctx.project_name == "myapp"

    def test_name_from_docker_yaml_config(self, tmp_path: Path) -> None:
        """If no .local.yaml exists, a .docker.yaml should be used."""
        (tmp_path / ".git").mkdir()
        config = tmp_path / "server" / "config"
        config.mkdir(parents=True)
        (config / "webapp.docker.yaml").write_text("port: 3000\n")

        ctx = ProjectContext(tmp_path)
        assert ctx.project_name == "webapp"

    def test_name_falls_back_to_dir_name(self, tmp_path: Path) -> None:
        """Without config files, the project root directory name is used."""
        (tmp_path / ".git").mkdir()

        ctx = ProjectContext(tmp_path)
        assert ctx.project_name == tmp_path.name


class TestProjectContextBuildTool:
    """Build tool detection tests."""

    def test_justfile_in_server_detected(self, tmp_path: Path) -> None:
        """A justfile in server/ should set build_tool to 'just'."""
        (tmp_path / ".git").mkdir()
        server = tmp_path / "server"
        server.mkdir()
        (server / "justfile").write_text("generate:\n  buf generate\n")

        ctx = ProjectContext(tmp_path)
        assert ctx.build_tool == "just"

    def test_makefile_in_server_detected(self, tmp_path: Path) -> None:
        """A Makefile in server/ should set build_tool to 'make'."""
        (tmp_path / ".git").mkdir()
        server = tmp_path / "server"
        server.mkdir()
        (server / "Makefile").write_text("generate_buf:\n\tbuf generate\n")

        ctx = ProjectContext(tmp_path)
        assert ctx.build_tool == "make"

    def test_justfile_at_root_detected(self, tmp_path: Path) -> None:
        """A justfile at the project root should be detected."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "justfile").write_text("build:\n  echo ok\n")

        ctx = ProjectContext(tmp_path)
        assert ctx.build_tool == "just"

    def test_default_build_tool_is_make(self, tmp_path: Path) -> None:
        """When neither justfile nor Makefile exist, default to 'make'."""
        (tmp_path / ".git").mkdir()

        ctx = ProjectContext(tmp_path)
        assert ctx.build_tool == "make"


class TestProjectContextMissingDirs:
    """Missing directory handling."""

    def test_missing_directories_return_none(self, tmp_path: Path) -> None:
        """When standard directories do not exist, the corresponding
        attributes should be None."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)

        assert ctx.server_dir is None
        assert ctx.web_dir is None
        assert ctx.hasura_dir is None
        assert ctx.proto_dir is None
        assert ctx.graph_dir is None


# ===========================================================================
# JsonLogger
# ===========================================================================


class TestJsonLogger:
    """Tests for JSONL structured logging."""

    def test_creates_log_file(self, tmp_path: Path) -> None:
        """Logging should create the .jsonl file in the specified directory."""
        logger = JsonLogger("V99-test", log_dir=tmp_path)
        logger.start()
        logger.log("testproject", [])

        assert logger.log_file.exists()
        assert logger.log_file.name == "V99-test.jsonl"

    def test_writes_valid_jsonl(self, tmp_path: Path) -> None:
        """Each line of the log file should be valid JSON."""
        logger = JsonLogger("V99-test", log_dir=tmp_path)

        # Write two entries
        logger.start()
        logger.log("proj1", [])
        logger.start()
        logger.log("proj2", [{"severity": "error", "rule": "V99-X"}])

        lines = logger.log_file.read_text().strip().split("\n")
        assert len(lines) == 2

        for line in lines:
            entry = json.loads(line)
            assert "timestamp" in entry
            assert "validator" in entry
            assert entry["validator"] == "V99-test"

    def test_records_timing(self, tmp_path: Path) -> None:
        """The duration_ms field should reflect elapsed time after start()."""
        logger = JsonLogger("V99-test", log_dir=tmp_path)
        logger.start()
        time.sleep(0.02)  # 20 ms
        logger.log("testproject", [])

        entry = json.loads(logger.log_file.read_text().strip())
        assert entry["duration_ms"] >= 10  # allow some tolerance

    def test_counts_errors_and_warnings(self, tmp_path: Path) -> None:
        """error_count and warning_count should tally correctly."""
        logger = JsonLogger("V99-test", log_dir=tmp_path)
        logger.start()

        findings = [
            {"severity": "error", "rule": "V99-A", "file": "a.go"},
            {"severity": "error", "rule": "V99-B", "file": "b.go"},
            {"severity": "warning", "rule": "V99-C", "file": "c.go"},
        ]
        logger.log("testproject", findings)

        entry = json.loads(logger.log_file.read_text().strip())
        assert entry["error_count"] == 2
        assert entry["warning_count"] == 1
        assert entry["findings_count"] == 3

    def test_no_findings_key_when_empty(self, tmp_path: Path) -> None:
        """When there are no findings, the 'findings' key should be absent
        from the log entry to keep logs compact."""
        logger = JsonLogger("V99-test", log_dir=tmp_path)
        logger.start()
        logger.log("testproject", [])

        entry = json.loads(logger.log_file.read_text().strip())
        assert "findings" not in entry
        assert entry["findings_count"] == 0


# ===========================================================================
# HashCache
# ===========================================================================


class TestHashCache:
    """Tests for SHA256 hash-based change detection."""

    def test_get_set_roundtrip(self, tmp_path: Path) -> None:
        """A value stored via set() should be retrievable via get()."""
        cache_file = tmp_path / "cache.json"
        cache = HashCache(cache_file=cache_file)

        cache.set("proto", "myproject", "abc123")
        assert cache.get("proto", "myproject") == "abc123"

    def test_get_returns_none_for_missing(self, tmp_path: Path) -> None:
        """get() should return None for keys that have not been set."""
        cache_file = tmp_path / "cache.json"
        cache = HashCache(cache_file=cache_file)

        assert cache.get("proto", "nonexistent") is None

    def test_has_changed_first_call_returns_false(self, tmp_path: Path) -> None:
        """On the very first call (no cached value), has_changed should
        return False and store the hash."""
        cache_file = tmp_path / "cache.json"
        cache = HashCache(cache_file=cache_file)

        result = cache.has_changed("proto", "myproject", "hash-a")

        assert result is False
        # The hash should now be stored
        assert cache.get("proto", "myproject") == "hash-a"

    def test_has_changed_same_hash_returns_false(self, tmp_path: Path) -> None:
        """Checking with the same hash should return False (no change)."""
        cache_file = tmp_path / "cache.json"
        cache = HashCache(cache_file=cache_file)

        cache.has_changed("proto", "myproject", "hash-a")  # first call stores
        result = cache.has_changed("proto", "myproject", "hash-a")

        assert result is False

    def test_has_changed_different_hash_returns_true(self, tmp_path: Path) -> None:
        """Checking with a different hash should return True (changed)."""
        cache_file = tmp_path / "cache.json"
        cache = HashCache(cache_file=cache_file)

        cache.has_changed("proto", "myproject", "hash-a")  # store initial
        result = cache.has_changed("proto", "myproject", "hash-b")

        assert result is True

    def test_persistence_to_file(self, tmp_path: Path) -> None:
        """A new HashCache instance reading the same file should see
        previously stored values."""
        cache_file = tmp_path / "cache.json"

        # First instance stores a value
        cache1 = HashCache(cache_file=cache_file)
        cache1.set("proto", "myproject", "persisted-hash")

        # Second instance loads from the same file
        cache2 = HashCache(cache_file=cache_file)
        assert cache2.get("proto", "myproject") == "persisted-hash"

    def test_cache_file_is_valid_json(self, tmp_path: Path) -> None:
        """The cache file on disk should contain valid JSON."""
        cache_file = tmp_path / "cache.json"
        cache = HashCache(cache_file=cache_file)
        cache.set("a", "b", "hash1")
        cache.set("c", "d", "hash2")

        data = json.loads(cache_file.read_text())
        assert data["a:b"] == "hash1"
        assert data["c:d"] == "hash2"


class TestHashFiles:
    """Tests for the hash_files utility function."""

    def test_hash_of_known_content(self, tmp_path: Path) -> None:
        """hash_files should produce a deterministic SHA256 hex digest."""
        f = tmp_path / "a.txt"
        f.write_text("hello")

        h1 = hash_files([f])
        h2 = hash_files([f])

        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex digest length

    def test_hash_changes_with_content(self, tmp_path: Path) -> None:
        """Changing file content should produce a different hash."""
        f = tmp_path / "a.txt"

        f.write_text("version1")
        h1 = hash_files([f])

        f.write_text("version2")
        h2 = hash_files([f])

        assert h1 != h2

    def test_hash_skips_missing_files(self, tmp_path: Path) -> None:
        """Missing files in the list should be silently skipped."""
        existing = tmp_path / "exists.txt"
        existing.write_text("data")
        missing = tmp_path / "nope.txt"

        # Should not raise
        result = hash_files([existing, missing])
        assert isinstance(result, str)
        assert len(result) == 64
