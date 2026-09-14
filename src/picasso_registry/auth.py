"""Shared service-auth helper for the DNA-PAINT FastAPI services.

Both **picasso-registry** and **monet** (WP-12a) import this one module so there
is a single audited auth implementation instead of two drifting copies. It
provides the three pieces the ratified auth model (ADR
``docs/adr/001-service-authentication.md``; Open-Decisions **C18**, "was A9")
calls for:

* a config/env-driven **token -> (scope, label) map** (:class:`AuthConfig`),
* a :func:`require_scope` FastAPI dependency enforcing the two capability
  scopes ``read`` (on ``GET``) and ``write`` (on ``POST``/bulk), and
* a fail-closed **host guard** (:func:`is_loopback_host`) so a service refuses
  to bind a non-loopback host unless tokens are configured.

Design invariants (from the ADR):

* **Loopback dev is zero-config.** With no tokens configured the service runs
  unauthenticated — allowed *only* on a loopback bind. ``RegistryClient`` with
  no ``token=`` sends no header, so the loopback dev path and the in-memory test
  mock keep working unchanged.
* **``write`` is a superset of ``read``.** A writer (an instrument logging
  provenance, the optimizer actuating a laser) also reads defaults/cohorts, so a
  ``write`` token satisfies a ``read`` requirement; a ``read`` token does not
  satisfy ``write``. This is capability separation, not per-user RBAC.
* **The token maps server-side to ``(scope, label)``.** ``label`` is a
  human-readable owner (a machine role like ``microscope-mercury``, or later a
  person); the indirection means the mechanism never changes as holders change.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# The two capability scopes and their rank. A required scope is satisfied by any
# token whose scope ranks at least as high (write >= read), so ``write`` implies
# ``read``.
_SCOPE_RANK = {"read": 0, "write": 1}

# Default env var the registry reads its token map from. monet passes its own
# (e.g. ``PAINT_MONET_TOKENS``) so the two services never share a token store.
DEFAULT_TOKENS_ENV = "PAINT_REGISTRY_TOKENS"


@dataclass(frozen=True)
class TokenInfo:
    """What a bearer token maps to server-side: a capability and an owner."""

    scope: str
    label: str


def parse_tokens(raw: str | None) -> dict[str, TokenInfo]:
    """Parse a ``TOKEN:SCOPE:LABEL`` token map from a config string.

    Entries are separated by commas, semicolons, or newlines; each entry is
    ``token:scope:label`` (``label`` may itself contain colons — only the first
    two colons are significant). ``scope`` must be ``read`` or ``write``.

    Misconfiguration fails closed: a malformed entry, an unknown scope, an empty
    token, or a duplicate token raises :class:`ValueError` rather than silently
    dropping a token (which would look like "auth on" while a writer is locked
    out, or leave an unintended token live).
    """
    tokens: dict[str, TokenInfo] = {}
    for entry in re.split(r"[,;\n]", raw or ""):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 2)
        if len(parts) != 3:
            raise ValueError(
                "malformed token entry (want 'token:scope:label'): "
                f"{entry!r}"
            )
        token, scope, label = (p.strip() for p in parts)
        if not token:
            raise ValueError(f"empty token in entry {entry!r}")
        if scope not in _SCOPE_RANK:
            raise ValueError(
                f"unknown scope {scope!r} (want 'read' or 'write')"
            )
        if not label:
            raise ValueError(f"empty label in entry {entry!r}")
        if token in tokens:
            raise ValueError("duplicate token in token map")
        tokens[token] = TokenInfo(scope=scope, label=label)
    return tokens


class AuthConfig:
    """The token -> ``(scope, label)`` map plus the enabled/disabled switch.

    When the map is empty the service is **unauthenticated** (``enabled`` is
    False) — the zero-config loopback dev path and the in-memory test mock. The
    fail-closed host guard (see :func:`is_loopback_host`) is what stops that
    state from ever being reachable on a networked bind.
    """

    def __init__(self, tokens: dict[str, TokenInfo] | None = None) -> None:
        self.tokens: dict[str, TokenInfo] = dict(tokens or {})

    @property
    def enabled(self) -> bool:
        return bool(self.tokens)

    @classmethod
    def from_env(cls, var: str = DEFAULT_TOKENS_ENV) -> "AuthConfig":
        """Build the config from ``var`` (default ``PAINT_REGISTRY_TOKENS``)."""
        return cls(parse_tokens(os.environ.get(var)))

    def resolve(self, token: str | None) -> TokenInfo | None:
        """Return the ``(scope, label)`` a token maps to, or None if unknown."""
        if token is None:
            return None
        return self.tokens.get(token)


def is_loopback_host(host: str) -> bool:
    """True if binding ``host`` never leaves the machine.

    ``localhost`` and any loopback IP (``127.0.0.0/8``, ``::1``) are loopback;
    ``0.0.0.0`` / ``::`` (all interfaces) and any real IP or hostname are not.
    Unresolvable/unknown hostnames are treated as non-loopback so the guard
    fails closed (refuses the bind) rather than open.
    """
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# One shared bearer extractor so FastAPI emits a single ``HTTPBearer`` security
# scheme in the OpenAPI spec. ``auto_error=False`` lets us own the 401/403
# responses (and the disabled/dev pass-through) instead of Starlette's default.
_bearer = HTTPBearer(
    auto_error=False,
    description=(
        "Static bearer token mapping server-side to a (scope, label). "
        "Omit on a loopback dev instance (auth disabled)."
    ),
)


def require_scope(required: str):
    """FastAPI dependency factory enforcing ``required`` (``read``/``write``).

    Attach as a route dependency (``dependencies=[Depends(require_scope(...))]``)
    so every route is guarded without changing handler signatures. Behaviour:

    * auth disabled (no tokens configured) -> allow (loopback dev / mock);
    * no/blank bearer token -> **401**;
    * unknown token -> **401**;
    * known token whose scope is below ``required`` -> **403**.

    On success it returns the :class:`TokenInfo` (so a handler *may* depend on it
    for the attributable ``label``), or None on the disabled path.
    """
    if required not in _SCOPE_RANK:
        raise ValueError(f"unknown scope {required!r}")

    def dependency(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> TokenInfo | None:
        config: AuthConfig = request.app.state.auth
        if not config.enabled:
            return None
        if credentials is None or not credentials.credentials:
            raise HTTPException(
                status_code=401,
                detail="missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        info = config.resolve(credentials.credentials)
        if info is None:
            raise HTTPException(
                status_code=401,
                detail="invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if _SCOPE_RANK[info.scope] < _SCOPE_RANK[required]:
            raise HTTPException(
                status_code=403,
                detail=f"token lacks required '{required}' scope",
            )
        return info

    return dependency
