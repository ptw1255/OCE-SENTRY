"""Azure CLI token acquisition.

`az login` is the entire auth story: no PATs, no token files, no secrets in
config. Tokens are cached per resource until shortly before expiry, because the
TUI refreshes on a timer and shelling out to `az` on every query would be both
slow and rate-limited.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


class AuthError(RuntimeError):
    """A token could not be acquired. The message names the fix."""


def _find_az() -> str:
    """Locate the Azure CLI.

    On Windows `az` is `az.cmd`, a batch shim rather than an executable, so
    subprocess cannot launch a bare "az" without a shell. Resolving the real
    path keeps shell=False, which is what keeps argument handling safe.
    """
    for candidate in ("az", "az.cmd", "az.exe"):
        found = shutil.which(candidate)
        if found:
            return found
    raise AuthError(
        "Azure CLI not found on PATH. Install it and run `az login`; it is the only "
        "{Credential} this console uses."
    )


@dataclass
class _CachedToken:
    token: str
    expires_at: datetime


class TokenProvider:
    """Thread-safe, per-resource token cache."""

    #: Refresh this far ahead of expiry. A long-running kit query started at the
    #: edge of a token's life would otherwise fail mid-flight.
    SKEW = timedelta(minutes=5)

    def __init__(self) -> None:
        self._cache: dict[str, _CachedToken] = {}
        self._lock = threading.Lock()
        self._az: str | None = None

    def _az_path(self) -> str:
        if self._az is None:
            self._az = _find_az()
        return self._az

    def signed_in_as(self) -> str:
        try:
            result = subprocess.run(
                [self._az_path(), "account", "show", "--query", "user.name", "-o", "tsv"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AuthError("`az account show` timed out after 60s.") from exc
        account = result.stdout.strip()
        if result.returncode != 0 or not account:
            raise AuthError("Azure CLI is not signed in. Run `az login`.")
        return account

    def invalidate(self, resource: str) -> None:
        with self._lock:
            self._cache.pop(resource, None)

    def token(self, resource: str) -> str:
        now = datetime.now(timezone.utc)
        with self._lock:
            cached = self._cache.get(resource)
            if cached and cached.expires_at - self.SKEW > now:
                return cached.token

        try:
            result = subprocess.run(
                [self._az_path(), "account", "get-access-token", "--resource", resource, "-o", "json"],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AuthError(f"Token request for {resource} timed out after 120s.") from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            hint = detail[-1] if detail else "no detail returned"
            raise AuthError(
                f"Could not get a token for {resource}.\n"
                f"  az said: {hint}\n"
                "  If this is a sign-in problem, run `az login`. If it is an access problem, "
                "this identity is not entitled to that resource."
            )

        try:
            payload = json.loads(result.stdout)
            token = payload["accessToken"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise AuthError(f"Unexpected `az account get-access-token` output for {resource}.") from exc

        expires_at = _parse_expiry(payload)
        with self._lock:
            self._cache[resource] = _CachedToken(token=token, expires_at=expires_at)
        return token


def _parse_expiry(payload: dict) -> datetime:
    """Best-effort expiry parse.

    `expiresOn` is local time without a zone; `expires_on` is a POSIX timestamp
    on newer CLIs. When neither parses, assume a short life rather than a long
    one -- re-fetching early is cheap, using an expired token is not.
    """
    epoch = payload.get("expires_on")
    if epoch is not None:
        try:
            return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass

    raw = payload.get("expiresOn")
    if raw:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                naive = datetime.strptime(raw, fmt)
            except ValueError:
                continue
            return naive.astimezone(timezone.utc)

    return datetime.now(timezone.utc) + timedelta(minutes=10)
