# Docker Validator Consolidation Design

**Date**: 2026-03-17
**Type**: Enhancement
**Status**: Approved

## Overview

Consolidate V05 (docker_compose.py) and V17 (docker_prod_deploy.py) into a single comprehensive Docker validator, while adding new validation rules from DOCKER_BEST_PRACTICES.md. This eliminates code duplication and provides a unified Docker validation experience through the verify-docker skill.

## Current State

- **V05 (docker_compose.py)**: Basic Compose validation (5 rules) - connected to verify-docker skill
- **V17 (docker_prod_deploy.py)**: Advanced Docker validation (9 rules) - standalone validator
- **verify-docker skill**: Only uses V05, missing advanced Dockerfile and production checks

## Proposed Architecture

### New V05 Structure
```python
class DockerValidator(BaseValidator):
    """V05: 통합 Docker 검증 (Compose + Dockerfile + Production)"""

    # 17 total validation rules:
    # - 5 existing V05 rules (compose-focused)
    # - 9 migrated V17 rules (dockerfile + production)
    # - 3 new rules from DOCKER_BEST_PRACTICES.md
```

### Validation Categories

#### Compose Validation (5 rules - existing V05)
- `V05-PORT-CONFLICT`: Service port conflicts
- `V05-VHOST-NO-NETWORK`: VIRTUAL_HOST without nginx-proxy network
- `V05-UNDEFINED-NETWORK`: Referenced network not defined
- `V05-MISSING-HEALTHCHECK`: depends_on service_healthy without healthcheck
- `V05-MISSING-ENV-VAR`: ${VAR} reference without .env definition

#### Dockerfile Validation (4 rules - migrated from V17)
- `V05-DOCKERFILE-NO-USER`: Production stage runs as root
- `V05-DOCKERFILE-NO-EXPOSE`: Missing EXPOSE directive
- `V05-DOCKERFILE-COPY-ALL`: COPY . . without .dockerignore
- `V05-DOCKERFILE-NO-MULTISTAGE`: Single-stage Dockerfile

#### Production Safety (3 rules - migrated from V17)
- `V05-PROD-PORT-EXPOSED`: Production ports exposed (should use Traefik)
- `V05-PROD-DEV-MODE`: Dev mode enabled in production
- `V05-PROD-WILDCARD-CORS`: CORS set to "*" in production

#### Development Setup (2 rules - migrated from V17)
- `V05-DEV-NO-VOLUME-MOUNT`: Dev override missing source mounts
- `V05-DEV-NO-BUILD-TARGET`: Dev override missing build target

#### New DOCKER_BEST_PRACTICES.md Rules (3 rules)
- `V05-BUILD-TARGET-MISSING`: build.target doesn't exist in Dockerfile
- `V05-BASE-IMAGE-LATEST`: Using latest tag (not recommended)
- `V05-MISSING-DOCKERIGNORE`: .dockerignore missing with COPY . .

## Implementation Details

### Method Structure
```python
def validate(self, ctx, file_path=None, mode="post_tool_use"):
    findings = []

    # 1. Discover files
    compose_files = self._find_compose_files(ctx)
    dockerfiles = self._find_dockerfiles(ctx)

    # 2. Validate compose files
    for compose_file in compose_files:
        findings.extend(self._validate_compose_file(ctx, compose_file))

    # 3. Validate dockerfiles
    for dockerfile in dockerfiles:
        findings.extend(self._validate_dockerfile(ctx, dockerfile))

    # 4. Cross-file validation
    findings.extend(self._validate_docker_integration(ctx, compose_files, dockerfiles))

    return ValidationResult(validator_id=self.id, findings=findings)
```

### New Validation Logic

#### V05-BUILD-TARGET-MISSING
Validates that `docker-compose.yml` `build.target` references actually exist in the target Dockerfile:

```python
def _check_build_target_exists(self, ctx, compose_files, dockerfiles):
    # Parse compose files for services with build.target
    # Find corresponding Dockerfile
    # Check if target stage exists using regex: r"^FROM\s+.*\s+AS\s+{target_name}\s*$"
```

#### V05-BASE-IMAGE-LATEST
Warns against using `:latest` tags or no tags (implicit latest):

```python
def _check_base_image_latest(self, dockerfile):
    # Patterns: FROM image:latest, FROM image AS stage (no tag)
    # Recommend specific versions like node:20-slim
```

#### V05-MISSING-DOCKERIGNORE
Ensures .dockerignore exists when `COPY . .` is used:

```python
def _check_dockerignore_exists(self, ctx, dockerfiles):
    # Scan Dockerfile for "COPY . ." pattern
    # Check if .dockerignore exists in same directory
```

## Migration Plan

### Phase 1: Code Consolidation
1. **Copy V17 methods to V05**: All dockerfile and production validation logic
2. **Update rule IDs**: Change V17-* → V05-* throughout the codebase
3. **Add new validations**: Implement 3 new DOCKER_BEST_PRACTICES.md rules
4. **Update file patterns**: Ensure V05 handles all Docker files

### Phase 2: Test Integration
1. **Merge test files**: Move `test_docker_prod_deploy.py` content to `test_docker_compose.py`
2. **Update test expectations**: Change V17-* rule IDs to V05-* in all assertions
3. **Add new test cases**: Cover 3 new validation rules
4. **Verify coverage**: Ensure all 17 rules have comprehensive tests

### Phase 3: Cleanup
1. **Delete V17 files**: Remove `docker_prod_deploy.py` and its tests
2. **Update skill description**: Expand verify-docker skill to list all 17 rules
3. **Clean logs**: Remove V17-related log files
4. **Verify integration**: Run full test suite and manual testing

### Phase 4: Documentation
1. **Update verify-docker skill**: Expand SKILL.md with complete rule list
2. **Update README**: Document the consolidated approach
3. **Commit changes**: Single atomic commit with clear message

## Risk Mitigation

### Pre-migration Checks
- Search codebase for direct V17 usage: `grep -r "V17\|docker_prod_deploy" .`
- Backup V17 files with `.backup` extension before deletion
- Run full test suite before starting migration

### Rollback Strategy
- Keep V17 `.backup` files until migration fully verified
- If critical issues found, restore from backup and revert V05 changes
- Gradual rollout: Test each phase completion before proceeding

### Testing Strategy
- Unit tests: Every validation method individually tested
- Integration tests: Full compose + dockerfile scenarios
- Edge cases: Malformed files, missing files, complex multi-stage builds
- Performance: Ensure consolidated validator doesn't significantly slow down

## Success Criteria

1. **Functionality**: All existing V05 and V17 validations work identically
2. **Coverage**: 17 total validation rules operational
3. **Performance**: No significant slowdown in validation time
4. **Usability**: verify-docker skill provides comprehensive Docker validation
5. **Maintainability**: Single codebase easier to maintain than two separate validators
6. **Testing**: 100% test coverage maintained through migration

## Dependencies

- Existing V05 and V17 validator implementations
- DOCKER_BEST_PRACTICES.md specifications
- verify-docker skill configuration
- Test suite infrastructure

## Timeline

- **Code consolidation**: ~2 hours
- **Test integration**: ~1 hour
- **Cleanup and documentation**: ~30 minutes
- **Total estimated time**: ~3.5 hours

This consolidation will significantly improve the Docker development experience by providing comprehensive validation through a single, well-integrated skill.