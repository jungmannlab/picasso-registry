"""Persist the admin GUI's connection profile to a 0600 dotfile.

Mirrors monet's convention of storing connection details in a private
per-user file rather than a bespoke profiles DB. We keep exactly one active
profile and **never** store the password (SSH keys/agent are the norm).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

#: Override with PR_ADMIN_PROFILE; defaults under the user's config dir.
_ENV = "PR_ADMIN_PROFILE"


def profile_path() -> str:
    override = os.environ.get(_ENV)
    if override:
        return override
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "picasso-registry-admin", "profile.json")


@dataclass
class Profile:
    host: str = ""
    user: str = ""
    port: int = 22
    identity: str = ""
    use_sudo: bool = False


def load_profile() -> Profile:
    """Return the saved profile, or defaults if none/unreadable."""
    try:
        with open(profile_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return Profile()
    allowed = {f for f in Profile().__dict__}
    return Profile(**{k: v for k, v in data.items() if k in allowed})


def save_profile(profile: Profile) -> None:
    """Write the profile 0600 (it names a host/user, not a secret, but keep
    it private anyway)."""
    path = profile_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Create 0600 before writing, then overwrite contents.
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(asdict(profile), fh, indent=2)
