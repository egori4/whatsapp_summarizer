from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Callable, Sequence

from .config import ConfigError, DigestConfig
from . import run


class CredentialError(RuntimeError):
    """The owner-controlled secret source cannot safely supply SMTP auth."""


def _read_exact_credential(source: Path, credential_key: str) -> str:
    """Read one credential from a regular owner-only env file without importing others."""
    if not credential_key or not credential_key.endswith("_PASSWORD"):
        raise CredentialError("credential key must be an explicit password environment reference")
    try:
        metadata = source.lstat()
    except FileNotFoundError as exc:
        raise CredentialError("credential source is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise CredentialError("credential source must be a regular file")
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise CredentialError("credential source must be owner-only")

    value: str | None = None
    for line in source.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, candidate = stripped.split("=", 1)
        if key.strip() != credential_key:
            continue
        if value is not None:
            raise CredentialError("credential source contains a duplicate password reference")
        candidate = candidate.strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"'}:
            candidate = candidate[1:-1]
        if not candidate:
            raise CredentialError("credential source password reference is empty")
        value = candidate
    if value is None:
        raise CredentialError("credential source password reference is unavailable")
    return value


def run_unattended(
    policy_path: Path,
    spool_path: Path,
    lock_path: Path,
    credential_source: Path,
    credential_key: str,
    *,
    runner: Callable[[Sequence[str]], int] = run.main,
) -> int:
    """Run the fixed delivery path with an ephemeral, mapped SMTP credential.

    The timer supplies neither recipient nor SMTP password.  The policy remains
    the sole recipient/transport authority; this wrapper maps only the named
    source password into the policy's password reference for the child call.
    """
    config = DigestConfig.from_dict(json.loads(policy_path.read_text(encoding="utf-8")))
    config.require_live_safe()
    secret = _read_exact_credential(credential_source, credential_key)
    policy_key = config.smtp.password_env
    previous = os.environ.get(policy_key)
    had_previous = policy_key in os.environ
    try:
        os.environ[policy_key] = secret
        return runner(
            [
                "--policy", str(policy_path),
                "--spool", str(spool_path),
                "--lock", str(lock_path),
                "--execution-mode", "delivery-capable",
            ]
        )
    finally:
        if had_previous:
            assert previous is not None
            os.environ[policy_key] = previous
        else:
            os.environ.pop(policy_key, None)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner-only unattended fixed-recipient digest wrapper")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--spool", required=True, type=Path)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--credential-source", required=True, type=Path)
    parser.add_argument("--credential-key", required=True)
    args = parser.parse_args(argv)
    try:
        return run_unattended(args.policy, args.spool, args.lock, args.credential_source, args.credential_key)
    except (CredentialError, ConfigError, OSError, ValueError):
        # Do not reveal policy values, source paths, or credential parsing detail
        # to a service journal.  A nonzero status retains the scheduler evidence.
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
