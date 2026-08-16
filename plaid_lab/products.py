"""Typed wrappers over the Plaid endpoints this project uses.

Each function takes a `PlaidApi` plus plain Python values and returns plain
data. Keeping the generated request models confined to this module means the
CLI and any later app code never import from `plaid.model.*`, so a library
version bump touches one file.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

from plaid.api.plaid_api import PlaidApi
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.auth_get_request import AuthGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.identity_get_request import IdentityGetRequest
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.institutions_get_request import InstitutionsGetRequest
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.investments_transactions_get_request import (
    InvestmentsTransactionsGetRequest,
)
from plaid.model.investments_transactions_get_request_options import (
    InvestmentsTransactionsGetRequestOptions,
)
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import (
    ItemPublicTokenExchangeRequest,
)
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.liabilities_get_request import LiabilitiesGetRequest
from plaid.model.link_token_create_hosted_link import LinkTokenCreateHostedLink
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_get_request import LinkTokenGetRequest
from plaid.model.products import Products
from plaid.model.sandbox_item_fire_webhook_request import (
    SandboxItemFireWebhookRequest,
)
from plaid.model.sandbox_item_reset_login_request import SandboxItemResetLoginRequest
from plaid.model.sandbox_public_token_create_request import (
    SandboxPublicTokenCreateRequest,
)
from plaid.model.sandbox_public_token_create_request_options import (
    SandboxPublicTokenCreateRequestOptions,
)
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.transactions_sync_request_options import (
    TransactionsSyncRequestOptions,
)
from plaid.model.webhook_type import WebhookType

from .client import call

# First Platypus Bank. The default Sandbox institution: supports the widest set
# of products and needs no OAuth flow.
DEFAULT_INSTITUTION = "ins_109508"


def _products(names: Iterable[str]) -> list[Products]:
    return [Products(name) for name in names]


def _countries(codes: Iterable[str]) -> list[CountryCode]:
    return [CountryCode(code) for code in codes]


# --- linking ---------------------------------------------------------------


def sandbox_public_token_create(
    client: PlaidApi,
    institution_id: str = DEFAULT_INSTITUTION,
    initial_products: Iterable[str] = ("transactions",),
    webhook: str | None = None,
) -> dict[str, Any]:
    """Mint a public_token directly, skipping the Link UI.

    Sandbox only. Plaid recommends this over automating Link in tests. The
    resulting Item is logged in as the universal Sandbox user (user_good).

    `webhook` has to be set here, at creation. `/sandbox/item/fire_webhook`
    fails with `ITEM_NOT_SUPPORTED` on an Item that has no webhook URL, and
    there is no way to attach one to an existing Item outside update mode.
    """
    kwargs: dict[str, Any] = dict(
        institution_id=institution_id,
        initial_products=_products(initial_products),
    )
    if webhook:
        kwargs["options"] = SandboxPublicTokenCreateRequestOptions(webhook=webhook)
    return call(
        client.sandbox_public_token_create, SandboxPublicTokenCreateRequest(**kwargs)
    )


def item_public_token_exchange(client: PlaidApi, public_token: str) -> dict[str, Any]:
    """Trade a short-lived public_token for the long-lived access_token."""
    return call(
        client.item_public_token_exchange,
        ItemPublicTokenExchangeRequest(public_token=public_token),
    )


def link_token_create(
    client: PlaidApi,
    client_user_id: str,
    client_name: str = "plaid-lab",
    products: Iterable[str] = ("transactions",),
    country_codes: Iterable[str] = ("US",),
    language: str = "en",
    webhook: str | None = None,
    hosted: bool = False,
    url_lifetime_seconds: int | None = None,
) -> dict[str, Any]:
    """Create a link_token, the input to one Link session.

    A link_token is NOT a credential for data -- it only configures the session.
    The ladder is link_token -> (browser) -> public_token -> access_token.

    With `hosted=True` the response also carries `hosted_link_url`: Plaid hosts
    the Link page itself, so no frontend is needed to reach an access_token.
    That makes this the practical Production path for a single-user app.
    """
    kwargs: dict[str, Any] = dict(
        client_name=client_name,
        language=language,
        country_codes=_countries(country_codes),
        user=LinkTokenCreateRequestUser(client_user_id=client_user_id),
        products=_products(products),
    )
    if webhook:
        kwargs["webhook"] = webhook
    if hosted:
        hosted_kwargs: dict[str, Any] = {}
        if url_lifetime_seconds:
            hosted_kwargs["url_lifetime_seconds"] = url_lifetime_seconds
        kwargs["hosted_link"] = LinkTokenCreateHostedLink(**hosted_kwargs)
    return call(client.link_token_create, LinkTokenCreateRequest(**kwargs))


def link_token_get(client: PlaidApi, link_token: str) -> dict[str, Any]:
    """Read back a link_token's sessions.

    `link_sessions` is absent until a session actually runs, and a finished one
    carries the public_token at
    `link_sessions[].results.item_add_results[].public_token`. Polling this is
    how a Hosted Link session hands back its result without a webhook receiver
    or a redirect URI.
    """
    return call(client.link_token_get, LinkTokenGetRequest(link_token=link_token))


def public_tokens_from_sessions(link_token_get_response: dict[str, Any]) -> list[str]:
    """Extract every public_token from a link_token_get response."""
    tokens: list[str] = []
    for session in link_token_get_response.get("link_sessions") or []:
        results = session.get("results") or {}
        for result in results.get("item_add_results") or []:
            token = result.get("public_token")
            if token:
                tokens.append(token)
    return tokens


# --- item ------------------------------------------------------------------


def item_get(client: PlaidApi, access_token: str) -> dict[str, Any]:
    return call(client.item_get, ItemGetRequest(access_token=access_token))


def item_remove(client: PlaidApi, access_token: str) -> dict[str, Any]:
    return call(client.item_remove, ItemRemoveRequest(access_token=access_token))


# --- accounts and balances -------------------------------------------------


def accounts_get(client: PlaidApi, access_token: str) -> dict[str, Any]:
    return call(client.accounts_get, AccountsGetRequest(access_token=access_token))


def accounts_balance_get(client: PlaidApi, access_token: str) -> dict[str, Any]:
    """Like accounts_get, but forces a fresh balance pull from the institution."""
    return call(
        client.accounts_balance_get,
        AccountsBalanceGetRequest(access_token=access_token),
    )


def auth_get(client: PlaidApi, access_token: str) -> dict[str, Any]:
    """Account and routing numbers for ACH."""
    return call(client.auth_get, AuthGetRequest(access_token=access_token))


def identity_get(client: PlaidApi, access_token: str) -> dict[str, Any]:
    """Account holder name, email, phone and address as the bank has them."""
    return call(client.identity_get, IdentityGetRequest(access_token=access_token))


# --- transactions ----------------------------------------------------------


MAX_DAYS_REQUESTED = 730


def transactions_sync(
    client: PlaidApi,
    access_token: str,
    cursor: str | None = None,
    count: int = 100,
    days_requested: int | None = MAX_DAYS_REQUESTED,
) -> dict[str, Any]:
    """Pull every page of /transactions/sync from `cursor` forward.

    /transactions/sync is the current endpoint -- /transactions/get is legacy and
    its date-window pagination has to be re-walked when anything changes.
    Sync returns added/modified/removed deltas plus a `next_cursor` to persist.

    **`days_requested` only takes effect when transactions are initialized for
    the first time**, and it cannot be widened afterwards -- an Item first
    synced under the 90-day default is capped at 90 days for its whole life, and
    the only remedy is re-linking. So it defaults to the 730-day maximum here
    rather than to Plaid's default. The institution may return less.

    Returns one merged page-set: `added`, `modified`, `removed`, `next_cursor`.
    """
    added: list[dict[str, Any]] = []
    modified: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []
    pages = 0

    while True:
        kwargs: dict[str, Any] = {"access_token": access_token, "count": count}
        if cursor:
            kwargs["cursor"] = cursor
        if days_requested:
            kwargs["options"] = TransactionsSyncRequestOptions(
                days_requested=days_requested
            )
        page = call(client.transactions_sync, TransactionsSyncRequest(**kwargs))
        pages += 1
        added.extend(page.get("added") or [])
        modified.extend(page.get("modified") or [])
        removed.extend(page.get("removed") or [])
        accounts = page.get("accounts") or accounts
        cursor = page.get("next_cursor")
        if not page.get("has_more"):
            break

    return {
        "added": added,
        "modified": modified,
        "removed": removed,
        "accounts": accounts,
        "next_cursor": cursor,
        "pages": pages,
    }


# --- investments and liabilities -------------------------------------------


def investments_holdings_get(client: PlaidApi, access_token: str) -> dict[str, Any]:
    return call(
        client.investments_holdings_get,
        InvestmentsHoldingsGetRequest(access_token=access_token),
    )


def investments_transactions_get(
    client: PlaidApi,
    access_token: str,
    start_date: dt.date,
    end_date: dt.date,
    page_size: int = 500,
) -> dict[str, Any]:
    """Pull every page of /investments/transactions/get over the window.

    Unlike /transactions/sync there is no `has_more` flag here: the endpoint
    caps a page at 500 (defaulting to 100) and reports the real count in
    `total_investment_transactions`. A caller that ignores that field gets a
    silently truncated list -- verified 2026-08-15, 100 rows returned against a
    total of 1170. Walk offsets until the accumulated count reaches the total.

    `securities` is deduplicated across pages; each page repeats the securities
    its own transactions reference.
    """
    transactions: list[dict[str, Any]] = []
    securities: dict[str, dict[str, Any]] = {}
    accounts: list[dict[str, Any]] = []
    total = 0
    pages = 0

    while True:
        page = call(
            client.investments_transactions_get,
            InvestmentsTransactionsGetRequest(
                access_token=access_token,
                start_date=start_date,
                end_date=end_date,
                options=InvestmentsTransactionsGetRequestOptions(
                    count=page_size, offset=len(transactions)
                ),
            ),
        )
        pages += 1
        batch = page.get("investment_transactions") or []
        transactions.extend(batch)
        for security in page.get("securities") or []:
            securities.setdefault(security["security_id"], security)
        accounts = page.get("accounts") or accounts
        total = page.get("total_investment_transactions") or len(transactions)
        # A short page means the window is exhausted; guard against a zero-length
        # page so an unexpected total can never spin this loop forever.
        if len(transactions) >= total or not batch:
            break

    return {
        "investment_transactions": transactions,
        "securities": list(securities.values()),
        "accounts": accounts,
        "total_investment_transactions": total,
        "pages": pages,
    }


def liabilities_get(client: PlaidApi, access_token: str) -> dict[str, Any]:
    """Credit card APRs, student loan and mortgage terms."""
    return call(
        client.liabilities_get, LiabilitiesGetRequest(access_token=access_token)
    )


# --- institutions ----------------------------------------------------------


def institutions_get_by_id(
    client: PlaidApi,
    institution_id: str,
    country_codes: Iterable[str] = ("US",),
) -> dict[str, Any]:
    return call(
        client.institutions_get_by_id,
        InstitutionsGetByIdRequest(
            institution_id=institution_id, country_codes=_countries(country_codes)
        ),
    )


def institutions_get(
    client: PlaidApi,
    count: int = 100,
    offset: int = 0,
    country_codes: Iterable[str] = ("US",),
) -> dict[str, Any]:
    return call(
        client.institutions_get,
        InstitutionsGetRequest(
            count=count, offset=offset, country_codes=_countries(country_codes)
        ),
    )


# --- sandbox simulation ----------------------------------------------------


def sandbox_item_reset_login(client: PlaidApi, access_token: str) -> dict[str, Any]:
    """Force the Item into ITEM_LOGIN_REQUIRED, as expired credentials would."""
    return call(
        client.sandbox_item_reset_login,
        SandboxItemResetLoginRequest(access_token=access_token),
    )


def sandbox_item_fire_webhook(
    client: PlaidApi,
    access_token: str,
    webhook_type: str = "TRANSACTIONS",
    webhook_code: str = "DEFAULT_UPDATE",
) -> dict[str, Any]:
    """Fire a webhook on demand. Requires a webhook URL set on the Item."""
    return call(
        client.sandbox_item_fire_webhook,
        SandboxItemFireWebhookRequest(
            access_token=access_token,
            webhook_type=WebhookType(webhook_type),
            webhook_code=webhook_code,
        ),
    )
