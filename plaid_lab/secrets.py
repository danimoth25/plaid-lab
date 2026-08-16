"""Secret storage in the OS credential store.

Everything sensitive lives here rather than on disk: the Plaid `client_id` and
`secret`, and every Item `access_token`. On Windows this resolves to
`WinVaultKeyring` -- the Credential Locker, encrypted under DPAPI against the
logged-in Windows account.

**What this buys and what it does not.** It defends against *file theft*: a
process that copies files off the machine gets nothing usable, and `.env` in
particular is a filename infostealers target by pattern. It does *not* defend
against code running as this user, which can call the same API and read the
same values. No scheme that decrypts without a human present can do better;
only a passphrase held outside the machine would, and a long-lived MCP server
cannot prompt for one.

The split is deliberate:

    .env        non-secret configuration (PLAID_ENV) -- safe to keep on disk
    keyring     client_id, per-environment secret, access tokens
    items.json  non-secret Item metadata: institution, products, cursor

Naming: secrets are per-environment because Plaid issues a different secret per
environment against one client_id, and having Sandbox and Production loaded at
once is normal.
"""

from __future__ import annotations

SERVICE = "plaid-lab"

CLIENT_ID = "client_id"


class KeyringUnavailable(RuntimeError):
    """The OS credential store could not be reached."""


def secret_key(environment: str) -> str:
    return f"secret:{environment}"


def access_token_key(item_id: str) -> str:
    return f"access_token:{item_id}"


def _keyring():
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise KeyringUnavailable(
            "The `keyring` package is not installed. Run: pip install -e ."
        ) from exc
    return keyring


def available() -> tuple[bool, str]:
    """Report whether a usable backend exists, and which one.

    Returns (ok, description). Never raises -- a status command that only works
    when everything is already fine is useless.
    """
    try:
        keyring = _keyring()
        backend = keyring.get_keyring()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    name = type(backend).__name__
    if "Fail" in name or "Null" in name:
        return False, f"{name} (no usable OS credential store)"
    return True, name


def get(name: str) -> str | None:
    try:
        return _keyring().get_password(SERVICE, name)
    except KeyringUnavailable:
        raise
    except Exception as exc:
        raise KeyringUnavailable(f"Could not read {name!r}: {exc}") from exc


def set(name: str, value: str) -> None:
    try:
        _keyring().set_password(SERVICE, name, value)
    except KeyringUnavailable:
        raise
    except Exception as exc:
        raise KeyringUnavailable(f"Could not store {name!r}: {exc}") from exc


def delete(name: str) -> bool:
    """Remove an entry. Returns False if it was not there; never raises on that."""
    keyring = _keyring()
    try:
        keyring.delete_password(SERVICE, name)
        return True
    except Exception:
        # keyring raises PasswordDeleteError for a missing entry, and the exact
        # class differs by backend, so absence is inferred rather than caught.
        return False
