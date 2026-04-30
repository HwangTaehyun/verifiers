# V48 — hasura-permission-rationale

> **Owner**: `hooks/validators/hasura_permission_rationale.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse)
> **File patterns**: `server/hasura/metadata/databases/*/tables/*.yaml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V48-HASURA-SELECT-ONLY-UNDOCUMENTED` | info | A table YAML has `select_permissions` for one or more roles but **no** `insert_permissions`, `update_permissions`, or `delete_permissions` AND no documented intent marker (repo-level or per-table comment). |

## Why this verifier exists

Hasura tables in this project grant only `select_permissions` to roles like `finance_admin`. Mutations (inserts, updates, deletes) are intentionally absent because writes go through gRPC handlers in the Connect-RPC service (`server/internal/...`), which execute business logic and validation that Hasura cannot enforce.

Evidence: `grep -rn "insert_permissions\|update_permissions\|delete_permissions" server/hasura/metadata/databases/*/tables/` returns zero hits. Every table has only `select_permissions`. This architectural choice is correct and intentional — but it's undocumented. A future engineer, seeing a table YAML with no mutation permissions, may assume it's an oversight and add `insert_permissions: [finance_admin]`, silently breaking the invariant that all writes go through gRPC.

V48 flags read-only tables and requires **either** (a) a project-level statement of intent (e.g., in `AGENTS.md` or `docs/architecture.md` mentioning `hasura-read-only` or `mutations-via-grpc`), **or** (b) an explicit per-table comment explaining the choice.

## Design rationale

- **Info severity, not warning.** The rule is informational: it surfaces a pattern that deserves documentation, but it's not a bug. The project may choose to document at the repo level and accept one global V48 flag rather than comment every table.
- **Detection is precise to Hasura YAML files.** Grep for the permission keys directly; if the keys are absent, the table is read-only in Hasura. This avoids false positives from role names or comments containing the words "insert" or "update".
- **Intent marker can be repo-level OR per-table.** Centralized projects document once (`AGENTS.md` mentions "writes go through gRPC"); distributed projects document per-table. V48 accepts both: it checks for either condition before flagging.
- **Comment location is flexible.** A YAML comment like `# mutations: intentionally absent — writes go through gRPC` on any line of the file is counted. Comments in `docs/` or `AGENTS.md` that reference `hasura-read-only` or `mutations-via-grpc` are counted as project-level intent.
- **Why this matters for future engineers.** A new developer cloning the repo and seeing only `select_permissions` might think it's incomplete. A clear marker ("this is by design") prevents confusion and accidental mutation-permission additions.

## How it checks (implementation plan)

Lives in `hooks/validators/hasura_permission_rationale.py`.

### Top-level

```python
def _all_checks(self, ctx):
    findings = []
    
    # Check if repo has project-level intent marker
    repo_intent = self._check_repo_level_intent(ctx)
    
    # Scan all table YAMLs
    for table_yaml in sorted(ctx.hasura_tables_dir.rglob("*.yaml")):
        findings.extend(self._check_table_intent(table_yaml, repo_intent))
    
    return findings
```

### `_check_repo_level_intent(ctx)` — Project-wide check

```python
def _check_repo_level_intent(self, ctx):
    """Returns True if any of AGENTS.md, README.md, or docs/*.md contains intent markers."""
    intent_markers = ("hasura-read-only", "mutations-via-grpc", "writes-via-grpc")
    
    for doc_file in [ctx.root / "AGENTS.md", ctx.root / "README.md"]:
        if doc_file.exists():
            text = doc_file.read_text().lower()
            if any(marker in text for marker in intent_markers):
                return True
    
    for doc in (ctx.root / "docs").glob("*.md"):
        if doc.exists():
            text = doc.read_text().lower()
            if any(marker in text for marker in intent_markers):
                return True
    
    return False
```

### `_check_table_intent(table_yaml, repo_intent)` — V48-HASURA-SELECT-ONLY-UNDOCUMENTED

```python
def _check_table_intent(self, table_yaml, repo_intent):
    data = yaml.safe_load(table_yaml.read_text())
    
    # If table has no select_permissions, not applicable
    if "select_permissions" not in data:
        return []
    
    # If table has any insert/update/delete permissions, it's not read-only
    if any(k in data for k in ["insert_permissions", "update_permissions", "delete_permissions"]):
        return []
    
    # Read-only table. Check for intent markers.
    text = table_yaml.read_text()
    intent_markers = ("mutations: intentionally absent", "writes go through", "grpc", "intent", "by design")
    
    if repo_intent or any(marker in text.lower() for marker in intent_markers):
        return []  # Intent is documented
    
    return [Finding(
        rule="V48-HASURA-SELECT-ONLY-UNDOCUMENTED",
        file=str(table_yaml),
        line=1,
        message="Table has only select_permissions; document why mutations are absent"
    )]
```

### Could be more effective

- **Per-role mutation intent.** Some roles may legitimately have insert but not update (e.g., `read_only` has select, `auditor` has select+insert for audit logs). Finer-grained intent tracking could handle this.
- **Validate gRPC handler coverage.** If the intent is "mutations go through gRPC", V48 could check that every mutation-related RPC (`CreateUser`, `UpdateOrder`, etc.) has a corresponding handler in `server/internal/`. This would ensure the architectural invariant is actually enforced.
- **Detect permission drift over time.** If a table *used to have* mutation permissions and they were removed, the comment should explain why. V48 could track this across migration versions.
- **Role-permission consistency.** All tables should either have permission sets for `finance_admin`, or for `public`, or for `user`. A table with permissions only for `finance_admin` while others use `user` is inconsistent. V48 could flag such patterns.

## References

- [Hasura — Understanding role-based access control](https://hasura.io/docs/2.0/auth/authorization/roles-permissions/) — Hasura, continuously updated, retrieved 2026-04-30. The permission model V48 assumes.
- [Hasura — Defining roles and permissions](https://hasura.io/docs/2.0/auth/authorization/permissions/) — Hasura, continuously updated, retrieved 2026-04-30. The per-table permission structure.
- [Connect-RPC — Implementing services](https://connectrpc.com/docs/go/serving-clients/) — Connect Authors, continuously updated, retrieved 2026-04-30. The alternative write path this project uses.

## Examples

### ✓ Pass

```yaml
# server/hasura/metadata/databases/default/tables/public_users.yaml
table:
  schema: public
  name: users
select_permissions:
  - role: finance_admin
    permission: {}
# Comments documenting the choice
# mutations: intentionally absent — writes go through gRPC handlers
```

```yaml
# Any table, if AGENTS.md states:
# "All Hasura tables are read-only. Mutations go through gRPC service."
table:
  schema: public
  name: orders
select_permissions:
  - role: finance_admin
    permission: {}
# (no mutation permissions needed; repo-level intent documented)
```

### ✗ Fail

```yaml
# server/hasura/metadata/databases/default/tables/public_payments.yaml
table:
  schema: public
  name: payments
select_permissions:
  - role: finance_admin
    permission: {}
# (no insert/update/delete permissions, no comment, no repo-level intent)
# → V48-HASURA-SELECT-ONLY-UNDOCUMENTED
```
