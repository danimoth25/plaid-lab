"""Configuration for the Plaid client.

Credentials come from a gitignored `.env` at the repo root, never from source.
Plaid issues a *different secret per environment* against the same client_id, so
the environment and the secret have to move together -- `PLAID_SECRET` is
whichever secret matches `PLAID_ENV`. Getting that pair wrong produces
`INVALID_API_KEYS`, which reads like a bad client_id and is not.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from . import secrets

REPO_ROOT = Path(__file__).resolve().parent.parent

# Plaid retired the Development environment in 2024; Sandbox and Production are
# the only hosts the current library ships. Kept as a dict so an unknown value
# in .env fails with a readable message instead of an AttributeError.
HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
}

# Pinned deliberately. Plaid versions its API by date and response shapes differ
# between versions; letting the account default apply makes replies depend on a
# dashboard setting rather than on this repo.
API_VERSION = "2020-09-14"


class ConfigError(RuntimeError):
    """Raised when credentials are missing or the environment name is unknown."""


@dataclass(frozen=True)
class Settings:
    client_id: str
    secret: str
    env: str
    #: Where each credential came from, for `secrets` / `env` to report.
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def host(self) -> str:
        return HOSTS[self.env]

    @property
    def is_sandbox(self) -> bool:
        return self.env == "sandbox"

    def masked(self) -> dict[str, str]:
        """Safe-to-print view. Never log or return the raw secret."""
        return {
            "client_id": f"{_mask(self.client_id)}  ({self.sources.get('client_id')})",
            "secret": f"{_mask(self.secret)}  ({self.sources.get('secret')})",
            "env": self.env,
            "host": self.host,
            "api_version": API_VERSION,
        }


def _mask(value: str) -> str:
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def load_settings() -> Settings:
    """Resolve credentials, preferring the OS credential store over `.env`.

    Precedence is keyring first so that migrating is a no-op switch: import the
    values, delete `.env`'s secret lines, and nothing else changes. `.env` stays
    supported as a fallback, and remains the right home for PLAID_ENV, which is
    configuration rather than a secret.
    """
    load_dotenv(REPO_ROOT / ".env")

    env = (os.getenv("PLAID_ENV") or "sandbox").strip().lower()
    if env not in HOSTS:
        raise ConfigError(
            f"PLAID_ENV={env!r} is not one of {sorted(HOSTS)}. "
            "Plaid removed the 'development' environment in 2024."
        )

    sources: dict[str, str] = {}

    def resolve(label: str, keyring_name: str, env_var: str) -> str:
        try:
            value = secrets.get(keyring_name)
        except secrets.KeyringUnavailable:
            value = None
        if value:
            sources[label] = "keyring"
            return value.strip()
        value = (os.getenv(env_var) or "").strip()
        if value:
            sources[label] = ".env"
            return value
        sources[label] = "missing"
        return ""

    client_id = resolve("client_id", secrets.CLIENT_ID, "PLAID_CLIENT_ID")
    secret = resolve("secret", secrets.secret_key(env), "PLAID_SECRET")

    missing = [n for n in ("client_id", "secret") if sources[n] == "missing"]
    if missing:
        raise ConfigError(
            f"Missing {', '.join(missing)} for environment {env!r}.\n"
            "Store them in the OS credential store:\n"
            "  python -m plaid_lab secrets set\n"
            "or put PLAID_CLIENT_ID / PLAID_SECRET in .env "
            "(keys: https://dashboard.plaid.com/developers/keys)"
        )

    return Settings(client_id=client_id, secret=secret, env=env, sources=sources)
