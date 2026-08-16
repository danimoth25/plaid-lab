"""Plaid API client construction and error handling.

Two things this module exists to normalize:

1. **Errors.** `plaid-python` raises `ApiException` with the useful part -- the
   `error_code` that tells you what to actually do -- buried in a JSON string on
   `.body`. Every caller would otherwise re-parse it. `PlaidError` pulls it out
   once and keeps the raw body for anything unanticipated.
2. **Responses.** Endpoint methods return generated model objects, not dicts.
   `.to_dict()` gives plain data but leaves `date`/`datetime` objects in place,
   which `json.dumps` refuses. `to_data()` handles both.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import plaid
from plaid.api import plaid_api

from .config import API_VERSION, Settings, load_settings


class PlaidError(RuntimeError):
    """A non-200 response from Plaid, with the error_code hoisted out."""

    def __init__(self, exc: plaid.ApiException):
        self.status = exc.status
        try:
            body = json.loads(exc.body or "{}")
        except (ValueError, TypeError):
            body = {}
        self.body: dict[str, Any] = body
        self.error_type: str = body.get("error_type") or "UNKNOWN"
        self.error_code: str = body.get("error_code") or f"HTTP_{exc.status}"
        self.error_message: str = body.get("error_message") or str(exc.reason or exc)
        self.display_message: str | None = body.get("display_message")
        self.request_id: str | None = body.get("request_id")
        self.causes: list[dict[str, Any]] = body.get("causes") or []
        super().__init__(f"{self.error_code}: {self.error_message}")

    def detail(self) -> str:
        lines = [
            f"  status       {self.status}",
            f"  error_type   {self.error_type}",
            f"  error_code   {self.error_code}",
            f"  message      {self.error_message}",
        ]
        if self.display_message:
            lines.append(f"  display      {self.display_message}")
        if self.request_id:
            lines.append(f"  request_id   {self.request_id}")
        for cause in self.causes:
            lines.append(f"  cause        {cause}")
        return "\n".join(lines)


def make_client(settings: Settings | None = None) -> plaid_api.PlaidApi:
    """Build a PlaidApi bound to the environment named in .env."""
    settings = settings or load_settings()
    configuration = plaid.Configuration(
        host=settings.host,
        api_key={
            "clientId": settings.client_id,
            "secret": settings.secret,
            "plaidVersion": API_VERSION,
        },
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def call(method, request) -> dict[str, Any]:
    """Invoke a PlaidApi method, returning plain JSON-safe data.

    Wraps every `ApiException` as `PlaidError` so callers see `error_code`
    rather than a stringified HTTP body.
    """
    try:
        response = method(request)
    except plaid.ApiException as exc:
        raise PlaidError(exc) from exc
    return to_data(response)


def to_data(value: Any) -> Any:
    """Recursively convert a Plaid model into JSON-serializable primitives."""
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        return {k: to_data(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_data(v) for v in value]
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def dumps(value: Any, indent: int = 2) -> str:
    return json.dumps(to_data(value), indent=indent, sort_keys=False, default=str)
