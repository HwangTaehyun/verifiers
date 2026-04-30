# V47 — fk-index-discipline

> **Owner**: `hooks/validators/fk_index_discipline.py` (planned, not yet implemented)
> **Tier**: 3 (Stop — critical for production safety)
> **File patterns**: `**/migrations/**/up.sql`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V47-FK-NO-INDEX` | error | A table column is declared with `REFERENCES <table>(<col>)` or `ADD CONSTRAINT … FOREIGN KEY (col)` has no `CREATE INDEX` covering that column anywhere in the migration history. |

## Why this verifier exists

PostgreSQL (unlike MySQL/InnoDB) does **not** automatically create indexes on foreign-key columns. When a `DELETE` or `UPDATE` is issued on the parent table, PostgreSQL must scan the child table(s) to check for referencing rows — without an index, this is a sequential scan.

Evidence: `server/hasura/migrations/default/1700000006000_rename_contracts_to_opportunities/up.sql:45,52,59,66,73` adds `FOREIGN KEY (opportunity_id) REFERENCES finance_opportunities(id)` to five tables (`contracts`, `contracts_audit`, `amendments`, `amendments_detail`, `invoice_items`). The migration creates zero indexes on `opportunity_id`. In production, a single `DELETE FROM finance_opportunities WHERE id = X` triggers five sequential scans, locking those tables until scan completes. Scaling to millions of rows, this becomes a multi-second operation that blocks all writes to those tables.

V47 catches this at commit-time: every FK column must have a `CREATE INDEX` in the same or an earlier migration.

## Design rationale

- **Error, not warning.** Foreign-key performance is not a style preference — it is a production stability guarantee. A missing FK index is a latent outage waiting to happen.
- **Index can live in any migration, including the FK migration itself.** The check is history-aware: if migration A adds the FK and migration B (later) adds the index, both are considered OK. The assumption is that the index will exist by the time production traffic starts.
- **Composite primary keys covering the FK column count.** If the child table has a composite primary key `(id, org_id, created_at)` and the FK is only `(opportunity_id)`, an index on just `opportunity_id` may not be sufficient for all lookups. V47 requires the index to cover the FK column(s); it doesn't enforce covering all PK columns.
- **One index per table per FK column set.** Multiple indexes on the same column are not flagged; the first match is enough.
- **Partial indexes (e.g., `WHERE is_deleted = FALSE`) are counted.** The index doesn't need to cover the full table; as long as it covers the FK column, it's counted as valid.

## How it checks (implementation plan)

Lives in `hooks/validators/fk_index_discipline.py`.

### Top-level

```python
def _all_checks(self, ctx):
    findings = []
    
    # Collect all FK constraints from migration history
    fks = self._collect_all_fks(ctx)  # List[(table, column, ref_table, ref_col)]
    
    # Collect all indexes from migration history
    indexes = self._collect_all_indexes(ctx)  # List[(table, columns)]
    
    # For each FK, check if an index exists
    findings.extend(self._check_fk_coverage(fks, indexes))
    
    return findings
```

### `_collect_all_fks(ctx)` — FK discovery

```python
def _collect_all_fks(self, ctx):
    fks = []
    FK_INLINE = re.compile(r"(\w+)\s+\w+.*?REFERENCES\s+(\w+)\s*\(\s*(\w+)\s*\)")
    FK_CONSTRAINT = re.compile(
        r"FOREIGN\s+KEY\s*\(\s*(\w+)\s*\)\s+"
        r"REFERENCES\s+(\w+)\s*\(\s*(\w+)\s*\)"
    )
    
    for up_file in sorted(ctx.migrations_dir.rglob("up.sql")):
        src = up_file.read_text()
        table = None
        
        # Parse CREATE TABLE to extract table name
        create_match = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", src)
        if create_match:
            table = create_match.group(1)
        
        # Inline REFERENCES declarations
        for m in FK_INLINE.finditer(src):
            col, ref_table, ref_col = m.group(1), m.group(2), m.group(3)
            if table:
                fks.append((table, col, ref_table, ref_col))
        
        # ALTER TABLE ADD CONSTRAINT FOREIGN KEY
        alter_match = re.search(r"ALTER\s+TABLE\s+(\w+)", src)
        if alter_match:
            table = alter_match.group(1)
        for m in FK_CONSTRAINT.finditer(src):
            col, ref_table, ref_col = m.group(1), m.group(2), m.group(3)
            fks.append((table, col, ref_table, ref_col))
    
    return fks
```

### `_collect_all_indexes(ctx)` — Index discovery

```python
def _collect_all_indexes(self, ctx):
    indexes = {}  # (table, column) -> True
    INDEX_PATTERN = re.compile(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+"
        r"ON\s+(\w+)\s*\(\s*([^)]+)\s*\)"
    )
    
    for up_file in sorted(ctx.migrations_dir.rglob("up.sql")):
        src = up_file.read_text()
        for m in INDEX_PATTERN.finditer(src):
            table = m.group(1)
            cols = [c.strip() for c in m.group(2).split(",")]
            for col in cols:
                indexes[(table, col)] = True
    
    return indexes
```

### `_check_fk_coverage(fks, indexes)` — V47-FK-NO-INDEX

```python
def _check_fk_coverage(self, fks, indexes):
    findings = []
    for table, col, ref_table, ref_col in fks:
        if (table, col) not in indexes:
            findings.append(Finding(
                rule="V47-FK-NO-INDEX",
                message=f"FK {table}({col}) → {ref_table}({ref_col}) has no index",
                file=str(up_file),
                line=1
            ))
    return findings
```

### Could be more effective

- **Validate index selectivity.** An index on `(status, opportunity_id)` where `status` has only 2 values may not help FK checks. Query-planner cost estimation would be more precise.
- **Detect partial-index blind spots.** A partial index `ON child_table(opportunity_id) WHERE status != 'deleted'` won't cover FK checks on deleted rows. Detecting and warning about such gaps would prevent subtle bugs.
- **Multi-column FK coverage.** If the FK is `(org_id, opportunity_id)`, V47 currently requires separate indexes on each. A composite index `(org_id, opportunity_id)` is better. Inferring composite FK structure from migration syntax would enable this check.
- **Cross-schema FKs.** Migrations in one schema referencing tables in another require explicit schema qualification. V47 doesn't yet handle schema-qualified names.

## References

- [PostgreSQL Wiki — Don't forget indexes on foreign keys](https://wiki.postgresql.org/wiki/Don%27t_Do_This#Don.27t_forget_indexes_on_foreign_keys) — PostgreSQL Community, continuously updated, retrieved 2026-04-30. The canonical reference for FK indexing.
- [PostgreSQL — Foreign Keys](https://www.postgresql.org/docs/current/ddl-constraints.html#DDL-CONSTRAINTS-FK) — PostgreSQL Documentation, continuously updated, retrieved 2026-04-30. The DDL constraints reference.
- [Use The Index, Luke! — Foreign Keys](https://use-the-index-luke.com/sql/join/foreign-keys) — Markus Winand, continuously updated, retrieved 2026-04-30. Performance implications of missing FK indexes.

## Examples

### ✓ Pass

```sql
-- migration 1: create parent and child tables with FK + index
CREATE TABLE finance_opportunities (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE payment_records (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT NOT NULL REFERENCES finance_opportunities(id),
    amount DECIMAL(10,2)
);

CREATE INDEX idx_payment_records_opportunity_id ON payment_records(opportunity_id);
```

```sql
-- migration 2: add FK in a later migration, index in same or later
ALTER TABLE invoices
    ADD CONSTRAINT fk_invoices_opportunity
    FOREIGN KEY (opportunity_id) REFERENCES finance_opportunities(id);

-- (same migration or later)
CREATE INDEX idx_invoices_opportunity_id ON invoices(opportunity_id);
```

### ✗ Fail

```sql
-- up.sql: add FK but no index
CREATE TABLE contracts (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT NOT NULL REFERENCES finance_opportunities(id),
    amount DECIMAL(10,2)
);

-- (no CREATE INDEX on opportunity_id)
-- → V47-FK-NO-INDEX
```

```sql
-- up.sql: add FK via constraint, no index anywhere in history
ALTER TABLE amendments
    ADD CONSTRAINT fk_amendments_opportunity
    FOREIGN KEY (opportunity_id) REFERENCES finance_opportunities(id);

-- (no CREATE INDEX)
-- → V47-FK-NO-INDEX
```
