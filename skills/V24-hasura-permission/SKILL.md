# V24 — hasura-permission

> **Owner**: `hooks/validators/hasura_permission.py`
> **Tier**: 2 (PostToolUse) per-file when the edited file is a Hasura `tables/*.yaml`. 3 (Stop) walks every metadata source.
> **File patterns**: `**/hasura/metadata/**/tables/**/*.yaml`, `**/hasura/metadata/**/tables/**/*.yml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V24-NO-PERMISSIONS` | error | A `tables/<schema>_<name>.yaml` has no `select_permissions` / `insert_permissions` / `update_permissions` / `delete_permissions` block (or all are empty lists). Only the admin secret can access the table. |
| `V24-WILDCARD-COLUMNS` | warning | A permission entry uses `columns: '*'` or `columns: ['*']`. Future columns (including secrets) are auto-exposed. |
| `V24-EMPTY-FILTER` | error | A `select_permissions` / `update_permissions` / `delete_permissions` entry on a non-admin role has `filter: {}` (empty). Every row is visible to that role — silent disablement of row-level security. |

V24 only fires when `hasura/metadata/databases/` exists. Projects without Hasura get zero findings.

## Why this verifier exists

Hasura's permission system is the *only* row-level / column-level security boundary between authenticated users and the database. Three failure patterns recur:

1. **Forgot to define permissions.** The developer adds a new table via migration, generates the metadata via `hasura metadata reload`, and ships. The table now exists in metadata with no role-based perms — meaning only admin can read it. If the front-end uses a non-admin JWT, every read returns empty / forbidden silently.
2. **`columns: '*'` shortcut.** During early development a permission gets written with `columns: '*'` ("just let user see everything"). Later, a `password_hash` column is added to the table — and is now visible via the public GraphQL API.
3. **Empty `filter: {}`.** Equivalent to "this role can read every row of this table". Only `admin` should have this; any other role with empty filter is the silent disablement of RLS — the user's home account ID stops being a row-level security boundary.

V24 turns each into a hook-time finding so the regression dies before metadata-apply.

## Design rationale

- **`V24-NO-PERMISSIONS` is `error`.** A table without role permissions is structurally inaccessible to non-admin users. Either the table is admin-internal (rare; flag visibly anyway) or someone forgot — both should block the metadata apply.
- **`V24-WILDCARD-COLUMNS` is `warning`.** Some tables legitimately want all columns visible (a public lookup table); the warning surfaces it for review without hard-failing.
- **`V24-EMPTY-FILTER` is `error`.** No legitimate non-admin use case for "every row visible" — if you genuinely want public access, use the `anonymous` role and the empty filter is OK *for anonymous*, but typically still demands an explicit `is_public: { _eq: true }` predicate. V24 conservatively errors and gives the `admin` exemption only.
- **Empty-filter exemption is role-name-based.** `_FILTER_EXEMPT_ROLES = {"admin", "internal-service"}`. Projects with a different elevated-role name need an override (TODO config knob). The exemption set is short by design — making it broader undermines the rule.
- **`columns` regex is `'*'` literal *or* `['*']` list.** Hasura accepts both. V24 catches both forms.
- **`insert_permissions` excluded from filter check.** Insert has no `filter:` (it's create-only); the equivalent shape for insert is `check:` and a missing/empty `check` is a different concern — not covered by V24 yet (potential follow-up).

## How it checks (implementation)

Lives in `hooks/validators/hasura_permission.py`.

### `_hasura_metadata_dirs(ctx)` — gating

```python
def _hasura_metadata_dirs(ctx):
    if ctx.hasura_dir is None:
        return []
    base = ctx.hasura_dir / "metadata" / "databases"
    if not base.is_dir():
        return []
    return [d for d in base.iterdir() if d.is_dir()]
```

### Tier 2 — `validate_file`

```python
def validate_file(self, ctx, file_path):
    if not _hasura_metadata_dirs(ctx):
        return []
    if "/tables/" not in file_path:
        return []
    return self._scan_table_yaml(Path(file_path))
```

### Tier 3 — `validate_project`

```python
def validate_project(self, ctx):
    findings = []
    for metadata_dir in _hasura_metadata_dirs(ctx):
        for table_file in _table_yaml_files(metadata_dir):
            findings.extend(self._scan_table_yaml(table_file))
    return findings
```

### `_scan_table_yaml(table_file)` — per-file core

```python
data = yaml.safe_load(table_file.read_text()) or {}
label = f"{data['table']['schema']}.{data['table']['name']}"

# (1) No permissions block at all
PERM_KEYS = ("select_permissions", "insert_permissions",
             "update_permissions", "delete_permissions")
has_any_perm = any(
    isinstance(data.get(k), list) and data[k] for k in PERM_KEYS
)
if not has_any_perm:
    yield Finding(rule="V24-NO-PERMISSIONS", ...)
    return  # don't pile on with column / filter findings

# (2) + (3) per-permission scan
for perm_key in PERM_KEYS:
    for entry in (data.get(perm_key) or []):
        role = entry.get("role")
        permission = entry.get("permission") or {}

        # (2) Wildcard columns
        cols = permission.get("columns")
        if cols == "*" or cols == ["*"]:
            yield Finding(rule="V24-WILDCARD-COLUMNS", ...)

        # (3) Empty row filter (skip insert; check select/update/delete)
        if perm_key in ("select_permissions",
                        "update_permissions",
                        "delete_permissions"):
            flt = permission.get("filter")
            if isinstance(flt, dict) and not flt and role not in EXEMPT_ROLES:
                yield Finding(rule="V24-EMPTY-FILTER", ...)
```

### Could be more effective

- **Permission-on-action.** `metadata/actions.yaml` defines custom actions; their `permissions:` block follows the same shape as table permissions. V24 currently doesn't reach actions — straightforward extension.
- **Cross-table consistency.** `users.session_token` and `sessions.token` columns probably share security policy; a real check would group columns by name across tables and warn when one is exposed and the other isn't. Heuristic; high signal when it triggers.
- **Column-allowlist sanity check.** A permission allowlist of `['id', 'name', 'password_hash']` is suspicious (password_hash should never be in a user-readable allowlist). A heuristic-based "did you really mean to expose this" check would help; needs a known-sensitive column list.
- **Inheritance-aware analysis.** Hasura roles can inherit; a child role inheriting an empty-filter parent is just as exposed. Currently V24 doesn't follow inheritance.
- **Per-project exempt-role config.** Projects use different vocabulary (`backend-service`, `staff`, `superuser`). Currently `admin` and `internal-service` are hardcoded; a `.verifiers/config.yaml` knob (`hasura_permission.exempt_roles`) would be a small addition.
- **Hasura Cloud API integration.** A *real* check is "fetch the live deployed metadata and verify it matches the source-of-truth in repo". Out of hook scope; CI-grade.

## References

- [Hasura — Authorization & Permissions](https://hasura.io/docs/2.0/auth/authorization/) — Hasura, *continuously updated*, retrieved 2026-04-30. The canonical statement of role / column / filter / check semantics V24 enforces.
- [Hasura — Configuring permissions](https://hasura.io/docs/2.0/auth/authorization/permissions/) — Hasura, *continuously updated*, retrieved 2026-04-30. Source of the four permission types and the `columns` / `filter` / `check` field shapes.
- [Hasura — Securing the GraphQL endpoint](https://hasura.io/docs/2.0/deployment/securing-graphql-endpoint/) — Hasura, *continuously updated*, retrieved 2026-04-30. Why role-based permissions are the only line of defense for non-admin access.
- [PostgreSQL — Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) — PostgreSQL, *continuously updated*, retrieved 2026-04-30. The general RLS pattern Hasura's `filter:` field maps to.
- [OWASP — Broken Access Control (A01:2021)](https://owasp.org/Top10/A01_2021-Broken_Access_Control/) — OWASP, *published 2021, continuously linked-to*, retrieved 2026-04-30. Why the empty-filter pattern is in the top OWASP risk category.

## Examples

### ✓ Pass

```yaml
# hasura/metadata/databases/default/tables/public_users.yaml
table:
  schema: public
  name: users
select_permissions:
  - role: anonymous
    permission:
      columns: ['id', 'display_name']      # allowlist ✓
      filter:
        is_public: { _eq: true }            # row predicate ✓
  - role: user
    permission:
      columns: ['id', 'display_name', 'email', 'created_at']
      filter:
        id: { _eq: X-Hasura-User-Id }        # row-level scope ✓
  - role: admin
    permission:
      columns: '*'                          # admin allowed wildcard
      filter: {}                            # admin allowed empty filter (exempt)
```

### ✗ Fail

```yaml
# Table with no perms at all → V24-NO-PERMISSIONS (error)
table:
  schema: public
  name: orders
```

```yaml
# Wildcard columns → V24-WILDCARD-COLUMNS (warning)
select_permissions:
  - role: anonymous
    permission:
      columns: '*'
      filter: { is_public: { _eq: true } }
```

```yaml
# Non-admin empty filter → V24-EMPTY-FILTER (error)
select_permissions:
  - role: user
    permission:
      columns: ['id']
      filter: {}                            # every row visible to every authenticated user
```
