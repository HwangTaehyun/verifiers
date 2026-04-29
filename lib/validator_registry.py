"""Shared resolver for the active validator set (Phase35, A1 audit).

Both ``hooks/router.py`` (Tier 2) and ``hooks/stop_validator.py``
(Tier 3) used to repeat the same four-step pipeline:

  1. ``get_all_validators()`` — pull the full registry.
  2. ``filter_enabled_validators(...)`` — apply the strict allowlist
     (empty = no filter). Raises ``ValueError`` on a non-empty list
     that matches zero validators (Phase22 hard-fail).
  3. catch the ValueError, ``log_exception``, emit a single
     ``VERIFIERS-CONFIG-EMPTY-ALLOWLIST`` finding so the user sees
     the typo instead of a silent approve.
  4. ``filter_disabled_validators(...)`` — apply the deny-list.

The pipeline drifted: router applied per-validator exclusion at the
end, stop_validator did not (Phase34/S1 already partially closed
that, but the wider risk was duplication anywhere).

This module exposes a single ``resolve_active_validators(ctx, *,
source)`` function the two hooks call. The Finding shape (severity,
rule, fix) is centralized here; the caller decides ``mode=`` and
formats the output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hooks.validators import get_all_validators
from hooks.validators.base import Finding
from lib.exclusion import filter_disabled_validators, filter_enabled_validators
from lib.json_logger import log_exception

if TYPE_CHECKING:
    from hooks.validators.base import BaseValidator
    from lib.project_context import ProjectContext


def resolve_active_validators(ctx: "ProjectContext", *, source: str) -> tuple[list["BaseValidator"], Finding | None]:
    """Compute the active validator set for ``ctx``.

    Args:
        ctx: ProjectContext carrying the loaded ``.verifiers/config.yaml``.
        source: Caller tag for ``log_exception`` (e.g.
            ``"router/filter_enabled_validators"``).

    Returns:
        ``(active, error_finding)``.

        - ``active`` is the validator list to dispatch over after
          allowlist + denylist filtering.
        - ``error_finding`` is ``None`` on success. When the user's
          ``validators.enabled`` is non-empty but matches zero
          registered ids (typo / stale entry), it is a single
          ``VERIFIERS-CONFIG-EMPTY-ALLOWLIST`` Finding the caller should
          emit verbatim and bail — running the empty list would silently
          approve every hook.
    """
    try:
        active = filter_enabled_validators(get_all_validators(), ctx.config.validators.enabled)
    except ValueError as exc:
        log_exception(
            source=source,
            error=exc,
            context={
                "cwd": str(ctx.cwd),
                "enabled": list(ctx.config.validators.enabled),
            },
        )
        finding = Finding(
            severity="error",
            file=str(ctx.project_root / ".verifiers" / "config.yaml"),
            rule="VERIFIERS-CONFIG-EMPTY-ALLOWLIST",
            message=str(exc),
            fix=(
                "Edit .verifiers/config.yaml: fix the typo in validators.enabled "
                "or remove the key entirely to run every validator."
            ),
        )
        return [], finding

    active = filter_disabled_validators(active, ctx.config.validators.disabled)
    return active, None
