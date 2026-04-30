# V46 — migration-enum-rollback

> **Owner**: `hooks/validators/migration_enum_rollback.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse)
> **File patterns**: `**/migrations/**/{up,down}.sql`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V46-ENUM-IRREVERSIBLE` | warning | `up.sql` contains `ALTER TYPE … ADD VALUE` but the paired `down.sql` neither contains an `ALTER TABLE` (rename-swap reversal) nor contains the marker `-- MANUAL ROLLBACK REQUIRED`. |

## Why this verifier exists

PostgreSQL (versions 1–16+) does not support `ALTER TYPE … DROP VALUE` — there is no way to remove an enum value once added. The canonical workaround is rename-swap: rename the old type, create a new one with the desired values, alter columns to the new type, and drop the old.

Evidence: `server/hasura/migrations/default/1700000003000_payment_model/up.sql:23-30` adds 4 enum values (`ONE_TIME`, `INSTALLMENT`, `BIANNUAL`, `CUSTOM`) to `finance_billing_cycle`. The paired `down.sql:1-20` contains only a comment: `-- Postgres does not support ALTER TYPE ... DROP VALUE`, with no rollback logic. If `down.sql` is ever executed (e.g., during a failed deploy recovery), it succeeds silently, leaving 4 orphan enum values in the database — subsequent upsert attempts will fail or silently corrupt data.

V46 catches this at hook-time: either the `down.sql` implements the rename-swap reversal (production-safe), or it explicitly declares `-- MANUAL ROLLBACK REQUIRED` so operators know to handle it by hand.

## Design rationale

- **Warning, not error.** Some projects legitimately accept irreversible migrations (e.g., read-only analytics pipelines where rollback is never tested). The flag alerts the team; enforcement is a policy choice.
- **Rename-swap is the canonical reversal.** The pattern `ALTER TYPE old_type RENAME TO old_type_legacy` → `CREATE TYPE old_type AS ENUM (...)` appears in most migration frameworks. V46 looks for the presence of `ALTER TABLE` (indicating column re-binding) as a strong signal that rename-swap was attempted.
- **Manual marker is explicit.** If the team consciously chooses irreversibility, `-- MANUAL ROLLBACK REQUIRED` on its own line signals intent. Future operators reading the migration will understand the constraints. This is machine-readable (operators searching for it) and human-readable (comments survive in backups).
- **Both conditions are OR, not AND.** The migration is safe if **either** a valid reversal exists **or** the manual marker is present. This respects both automated and manual recovery workflows.

## How it checks (implementation plan)

Lives in `hooks/validators/migration_enum_rollback.py`.

### Top-level

```python
def _all_checks(self, ctx):
    findings = []
    for migration_dir in ctx.migrations_dir.glob("*/"):
        up_file = migration_dir / "up.sql"
        down_file = migration_dir / "down.sql"
        if up_file.exists() and down_file.exists():
            findings.extend(self._check_enum_reversible(up_file, down_file))
    return findings
```

### `_check_enum_reversible(up_file, down_file)` — V46-ENUM-IRREVERSIBLE

```python
def _check_enum_reversible(self, up_file, down_file):
    up_text = up_file.read_text()
    down_text = down_file.read_text()
    
    # If up.sql has no ALTER TYPE ADD VALUE, skip
    if not re.search(r"ALTER\s+TYPE\s+\w+\s+ADD\s+VALUE", up_text):
        return []
    
    # If down.sql has the manual marker, it's acceptable
    if "-- MANUAL ROLLBACK REQUIRED" in down_text:
        return []
    
    # If down.sql has ALTER TABLE (rename-swap indicator), assume reversal exists
    if re.search(r"ALTER\s+TABLE\s+\w+\s+ALTER\s+COLUMN", down_text):
        return []
    
    # Otherwise flag it
    return [Finding(
        rule="V46-ENUM-IRREVERSIBLE",
        file=str(down_file),
        line=1,
        message="ALTER TYPE ADD VALUE without reversible down.sql"
    )]
```

### Could be more effective

- **Validate rename-swap structure.** Currently V46 looks for `ALTER TABLE` as a proxy. A stricter check would parse the full sequence: `ALTER TYPE old RENAME TO old_legacy` followed by `CREATE TYPE old AS ENUM (...)` followed by `ALTER TABLE ...`. This would catch incomplete or out-of-order reversals.
- **Detect enum value collisions.** If `down.sql` does implement rename-swap but forgets a value that was in the original enum, subsequent upserts will fail. Parsing enum definitions to compare them would catch this.
- **Cross-migration enum tracking.** An enum added in migration A, modified in B, then rolled back in B only would leave A's values dangling. Full project-level enum schema reconstruction would detect this.
- **Suggest enum-preservation patterns.** For irreversible enums, suggest adding a `deprecated_at` column and a deprecation comment instead of `DROP VALUE`.

## References

- [PostgreSQL — ALTER TYPE](https://www.postgresql.org/docs/current/sql-altertype.html) — PostgreSQL Documentation, continuously updated, retrieved 2026-04-30. The reference showing `DROP VALUE` is not supported.
- [PostgreSQL Wiki — Don't forget indexes on foreign keys](https://wiki.postgresql.org/wiki/Don%27t_Do_This#Don.27t_forget_indexes_on_foreign_keys) — PostgreSQL Community, continuously updated, retrieved 2026-04-30. The enum section discusses rename-swap as the canonical workaround.
- [Hasura migrations guide](https://hasura.io/docs/latest/migrations-metadata-seeds/manage-migrations/) — Hasura, continuously updated, retrieved 2026-04-30. The context in which these migrations exist.

## Examples

### ✓ Pass

```sql
-- up.sql
ALTER TYPE finance_billing_cycle ADD VALUE 'ONE_TIME';
ALTER TYPE finance_billing_cycle ADD VALUE 'INSTALLMENT';
```

```sql
-- down.sql with manual marker
-- MANUAL ROLLBACK REQUIRED: PostgreSQL does not support DROP VALUE.
-- To revert: run migrations before this one.
```

```sql
-- down.sql with rename-swap reversal
ALTER TYPE finance_billing_cycle RENAME TO finance_billing_cycle_v1;
CREATE TYPE finance_billing_cycle AS ENUM ('MONTHLY', 'YEARLY');
ALTER TABLE payment_records ALTER COLUMN billing_cycle TYPE finance_billing_cycle USING billing_cycle::text::finance_billing_cycle;
DROP TYPE finance_billing_cycle_v1;
```

### ✗ Fail

```sql
-- up.sql
ALTER TYPE finance_billing_cycle ADD VALUE 'ONE_TIME';
ALTER TYPE finance_billing_cycle ADD VALUE 'INSTALLMENT';
```

```sql
-- down.sql with no reversal logic
-- Postgres does not support ALTER TYPE ... DROP VALUE
-- This migration is irreversible.
-- → V46-ENUM-IRREVERSIBLE
```
