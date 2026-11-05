"""picasso-registry admin tooling (the ``[admin]`` extra).

An SSH-driven admin console for a deployed picasso-registry: it runs the
server-side ``picasso-registry-admin`` wrapper over SSH to manage tokens, check
status, tail logs, and upgrade — so the registry keeps **no network admin
surface** (authority is your SSH access, per ADR 001).

Layers, mirroring monet's remote-setup wizard:

* :mod:`picasso_registry.admin.core` — GUI-free SSH transport + operations
  (paramiko, lazy-imported). The one place that knows how to drive the box.
* :mod:`picasso_registry.admin.cli` — headless front-end
  (``picasso-registry-admin-cli``).
* :mod:`picasso_registry.admin.qt` — PyQt6 GUI
  (``picasso-registry-admin-gui``).

Both front-ends are thin renderers over the core. Qt and paramiko live in the
``[admin]`` extra; the base package stays dependency-free.
"""

from __future__ import annotations

from .core import (
    AdminError,
    RegistryAdmin,
    ServerStatus,
    TokenEntry,
    parse_minted_token,
    parse_status,
    parse_token_list,
)

__all__ = [
    "RegistryAdmin",
    "AdminError",
    "ServerStatus",
    "TokenEntry",
    "parse_status",
    "parse_token_list",
    "parse_minted_token",
]
