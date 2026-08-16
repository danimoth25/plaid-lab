"""Configuration for the Plaid client.

Credentials come from a gitignored `.env` at the repo root, never from source.
Plaid issues a *different secret per environment* against the same client_id, so
the environment and the secret have to move together -- `PLAID_SECRET` is
whichever secret matches `PLAID_ENV`. Getting that pair wrong produces
`INVALID_API_KEYS`, which reads like a bad client_id and is not.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

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

    @property
    def host(self) -> str:
        return HOSTS[self.env]

    @property
    def is_sandbox(self) -> bool:
        return self.env == "sandbox"

    def masked(self) -> dict[str, str]:
        """Safe-to-print view. Never log or return the raw secret."""
        return {
            "client_id": _mask(self.client_id),
            "secret": _mask(self.secret),
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
    load_dotenv(REPO_ROOT / ".env")

    env = (os.getenv("PLAID_ENV") or "sandbox").strip().lower()
    if env not in HOSTS:
        raise ConfigError(
            f"PLAID_ENV={env!r} is not one of {sorted(HOSTS)}. "
            "Plaid removed the 'development' environment in 2024."
        )

    client_id = (os.getenv("PLAID_CLIENT_ID") or "").strip()
    secret = (os.getenv("PLAID_SECRET") or "").strip()
    missing = [
        name
        for name, value in (("PLAID_CLIENT_ID", client_id), ("PLAID_SECRET", secret))
        if not value
    ]
    if missing:
        raise ConfigError(
            f"Missing {', '.join(missing)}. Copy .env.example to .env and fill in "
            "the keys from https://dashboard.plaid.com/developers/keys"
        )

    return Settings(client_id=client_id, secret=secret, env=env)
