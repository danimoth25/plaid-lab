"""Command line for poking at the Plaid API.

    python -m plaid_lab <command> [options]

Every command that touches an Item takes `--item` to select one; with a single
linked Item it can be omitted. `--json` prints the raw response instead of a
table, which is the point of the tool -- the tables are a convenience, the JSON
is what you actually build against.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import os
import sys
import time
from typing import Any, Callable

import plaid
from dotenv import load_dotenv

from . import fmt, products, secrets
from .client import PlaidError, dumps, make_client
from .config import REPO_ROOT, ConfigError, Settings, load_settings
from .store import Item, ItemStore, StoreError


class Context:
    """Lazily-built settings, client and Item store shared by every command."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self._settings: Settings | None = None
        self._client = None
        self._store: ItemStore | None = None

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = load_settings()
        return self._settings

    @property
    def client(self):
        if self._client is None:
            self._client = make_client(self.settings)
        return self._client

    @property
    def store(self) -> ItemStore:
        if self._store is None:
            self._store = ItemStore()
        return self._store

    def item(self) -> Item:
        return self.store.select(self.settings.env, getattr(self.args, "item", None))

    def require_sandbox(self, command: str) -> None:
        if not self.settings.is_sandbox:
            raise ConfigError(
                f"`{command}` is a Sandbox-only endpoint; PLAID_ENV is "
                f"{self.settings.env}."
            )

    def emit(self, data: Any, render: Callable[[], None]) -> None:
        """Print raw JSON or the rendered view, depending on --json."""
        if self.args.json:
            print(dumps(data))
        else:
            render()


# --- commands --------------------------------------------------------------


def cmd_env(ctx: Context) -> int:
    """Show the resolved configuration and confirm the keys authenticate."""
    settings = ctx.settings
    for key, value in settings.masked().items():
        print(f"{key:14} {value}")

    # /institutions/get needs only client credentials, so it is the cheapest
    # proof that the client_id and the environment-specific secret match.
    try:
        response = products.institutions_get(ctx.client, count=1)
    except PlaidError as exc:
        print("\nauth           FAILED")
        print(exc.detail())
        return 1
    print(f"\nauth           ok ({response.get('total')} institutions visible)")

    items = ctx.store.for_env(settings.env)
    print(f"linked items   {len(items)}")
    return 0


def cmd_secrets(ctx: Context) -> int:
    """Inspect and manage credentials in the OS credential store."""
    action = ctx.args.action
    ok, backend = secrets.available()
    print(f"backend        {backend}")
    if not ok:
        print(
            "\nNo usable OS credential store. Credentials will fall back to "
            ".env.",
            file=sys.stderr,
        )
        if action != "status":
            return 1

    if action == "status":
        return _secrets_status(ctx)
    if action == "migrate":
        return _secrets_migrate(ctx)
    if action == "set":
        return _secrets_set(ctx)
    if action == "clear":
        return _secrets_clear(ctx)
    raise ConfigError(f"unknown action {action!r}")


def _secrets_status(ctx: Context) -> int:
    rows = []
    for env_name in ("sandbox", "production"):
        key = secrets.secret_key(env_name)
        try:
            present = bool(secrets.get(key))
        except secrets.KeyringUnavailable:
            present = False
        rows.append([f"secret ({env_name})", "keyring" if present else "-"])

    try:
        client_id_present = bool(secrets.get(secrets.CLIENT_ID))
    except secrets.KeyringUnavailable:
        client_id_present = False
    rows.insert(0, ["client_id", "keyring" if client_id_present else "-"])

    store = ctx.store
    for item in store.items:
        rows.append(
            [
                f"access_token ({item.label()})",
                "keyring" if item.has_access_token() else "-",
            ]
        )

    print(fmt.heading("credential store"))
    print(fmt.table(rows, ["entry", "location"]))

    dotenv_path = REPO_ROOT / ".env"
    print(fmt.heading("plaintext on disk"))
    leftovers = []
    if dotenv_path.exists():
        text = dotenv_path.read_text(encoding="utf-8")
        for var in ("PLAID_CLIENT_ID", "PLAID_SECRET"):
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{var}=") and stripped != f"{var}=":
                    leftovers.append(f".env  {var}")
    if store.legacy_tokens:
        for item_id in store.legacy_tokens:
            leftovers.append(f"items.json  access_token for {item_id[:12]}...")
    print("\n".join(f"  {row}" for row in leftovers) if leftovers else "  (none)")
    if leftovers:
        print("\nRun `secrets migrate` to move these into the credential store.")
    return 0


def _secrets_migrate(ctx: Context) -> int:
    """One-time import of credentials from .env and items.json into keyring."""
    moved = []

    load_dotenv(REPO_ROOT / ".env")
    env_name = ctx.settings.env if ctx.args.env is None else ctx.args.env

    client_id = (os.getenv("PLAID_CLIENT_ID") or "").strip()
    if client_id and not secrets.get(secrets.CLIENT_ID):
        secrets.set(secrets.CLIENT_ID, client_id)
        moved.append("client_id")

    secret = (os.getenv("PLAID_SECRET") or "").strip()
    if secret and not secrets.get(secrets.secret_key(env_name)):
        secrets.set(secrets.secret_key(env_name), secret)
        moved.append(f"secret ({env_name})")

    store = ctx.store
    for item_id, token in store.legacy_tokens.items():
        secrets.set(secrets.access_token_key(item_id), token)
        moved.append(f"access_token ({item_id[:12]}...)")
    if store.legacy_tokens:
        # Rewriting items.json drops the access_token fields, since Item no
        # longer carries one.
        store.save()

    if not moved:
        print("\nNothing to migrate; everything is already in the credential store.")
        return 0

    print(fmt.heading("moved into the credential store"))
    for name in moved:
        print(f"  {name}")
    print(
        "\nitems.json has been rewritten without access tokens."
        "\n\nNow delete the secret lines from .env by hand -- this command will "
        "not edit it,\nbecause a botched rewrite of the only copy of a "
        "credential is unrecoverable.\nKeep PLAID_ENV; it is configuration, not "
        "a secret."
    )
    return 0


def _secrets_set(ctx: Context) -> int:
    """Prompt for a credential and store it. Never echoes, never takes argv."""
    name = ctx.args.name
    env_name = ctx.args.env or ctx.settings.env if name == "secret" else None
    key = secrets.CLIENT_ID if name == "client_id" else secrets.secret_key(env_name)
    label = name if name == "client_id" else f"{name} ({env_name})"

    # getpass, not an argument: a value on the command line lands in shell
    # history and in the process list.
    value = getpass.getpass(f"Paste {label} (input hidden): ").strip()
    if not value:
        print("Nothing entered; unchanged.", file=sys.stderr)
        return 1
    secrets.set(key, value)
    print(f"stored {label} in the OS credential store")
    return 0


def _secrets_clear(ctx: Context) -> int:
    """Remove every entry this project owns."""
    removed = []
    if secrets.delete(secrets.CLIENT_ID):
        removed.append("client_id")
    for env_name in ("sandbox", "production"):
        if secrets.delete(secrets.secret_key(env_name)):
            removed.append(f"secret ({env_name})")
    for item in ctx.store.items:
        if item.forget_access_token():
            removed.append(f"access_token ({item.item_id[:12]}...)")
    print(fmt.heading("removed"))
    print("\n".join(f"  {r}" for r in removed) if removed else "  (nothing stored)")
    if removed:
        print(
            "\nItems still exist at Plaid. Use `remove` to revoke them there, "
            "or `import-token` to restore access."
        )
    return 0


def cmd_institutions(ctx: Context) -> int:
    """List institutions available in this environment."""
    data = products.institutions_get(
        ctx.client, count=ctx.args.count, country_codes=ctx.args.country
    )

    def render() -> None:
        rows = [
            [
                inst.get("institution_id"),
                inst.get("name"),
                ",".join(inst.get("products") or [])[:60],
            ]
            for inst in data.get("institutions") or []
        ]
        print(fmt.heading(f"institutions ({data.get('total')} total)"))
        print(fmt.table(rows, ["institution_id", "name", "products"]))

    ctx.emit(data, render)
    return 0


def cmd_link(ctx: Context) -> int:
    """Create a Sandbox Item without the Link UI, and store its access_token."""
    ctx.require_sandbox("link")
    product_list = ctx.args.products
    institution_id = ctx.args.institution

    public = products.sandbox_public_token_create(
        ctx.client,
        institution_id=institution_id,
        initial_products=product_list,
        webhook=ctx.args.webhook,
    )
    item = _store_public_token(ctx, public["public_token"])
    print(f"linked {item.label()}")
    print(f"  products     {', '.join(product_list)}")
    print(f"  stored in    {ctx.store.path}")
    return 0


def cmd_import_token(ctx: Context) -> int:
    """Adopt an access_token created elsewhere into the local store.

    Tokens made through the dashboard, the Quickstart, or another machine are
    perfectly usable here -- an access_token is scoped to the client_id that
    created it, not to the process. Everything the store needs beyond the token
    itself (item_id, institution, products) is readable from /item/get, so the
    only required argument is the token.
    """
    token = ctx.args.token
    body = products.item_get(ctx.client, token).get("item") or {}
    existing = next(
        (i for i in ctx.store.items if i.item_id == body.get("item_id")), None
    )
    # ItemStore.add preserves an existing cursor, so re-importing an Item does
    # not replay its whole transaction history.
    item = _record_item(ctx, body["item_id"], token)

    verb = "updated" if existing else "imported"
    print(f"{verb} {item.label()}")
    print(f"  products     {', '.join(item.products) or '(none)'}")
    error = body.get("error")
    if error:
        print(f"  error        {error.get('error_code')}")
    print(f"  token in     OS credential store")
    print(f"  metadata in  {ctx.store.path}")
    return 0


def cmd_link_token(ctx: Context) -> int:
    """Create a link_token for the real Link UI (the Production path)."""
    data = products.link_token_create(
        ctx.client,
        client_user_id=ctx.args.user,
        products=ctx.args.products,
        country_codes=ctx.args.country,
        webhook=ctx.args.webhook,
    )

    def render() -> None:
        print(f"link_token   {data.get('link_token')}")
        print(f"expiration   {data.get('expiration')}")
        print(f"request_id   {data.get('request_id')}")

    ctx.emit(data, render)
    return 0


def _record_item(ctx: Context, item_id: str, access_token: str) -> Item:
    """Persist an Item: token to the credential store, metadata to items.json.

    The token is written first. If the metadata write then fails the token is
    merely orphaned in the keyring, which is recoverable; the reverse order
    would leave an Item recorded with no way to reach it.
    """
    body = products.item_get(ctx.client, access_token).get("item") or {}
    institution_id = body.get("institution_id")
    name = body.get("institution_name")
    if not name and institution_id:
        try:
            detail = products.institutions_get_by_id(ctx.client, institution_id)
            name = (detail.get("institution") or {}).get("name")
        except PlaidError:
            pass

    item = Item(
        item_id=item_id,
        environment=ctx.settings.env,
        institution_id=institution_id,
        institution_name=name,
        products=list(body.get("products") or []),
    )
    item.store_access_token(access_token)
    return ctx.store.add(item)


def _store_public_token(ctx: Context, public_token: str) -> Item:
    """Exchange a public_token and record the resulting Item."""
    exchanged = products.item_public_token_exchange(ctx.client, public_token)
    return _record_item(ctx, exchanged["item_id"], exchanged["access_token"])


def _claim(ctx: Context, link_token: str) -> list[Item]:
    """Exchange every public_token a link_token's sessions have produced."""
    session_data = products.link_token_get(ctx.client, link_token)
    claimed = []
    for public_token in products.public_tokens_from_sessions(session_data):
        claimed.append(_store_public_token(ctx, public_token))
    return claimed


def cmd_hosted_link(ctx: Context) -> int:
    """Create a Hosted Link session -- Plaid hosts the page, so no frontend.

    This is the only path in the repo that reaches an access_token the way
    Production requires: a real browser, real credentials typed into Plaid.
    In Sandbox the credentials are user_good / pass_good.
    """
    data = products.link_token_create(
        ctx.client,
        client_user_id=ctx.args.user,
        products=ctx.args.products,
        country_codes=ctx.args.country,
        webhook=ctx.args.webhook,
        hosted=True,
        url_lifetime_seconds=ctx.args.lifetime,
    )
    if ctx.args.json:
        print(dumps(data))
        return 0

    link_token = data["link_token"]
    print(f"open this in a browser:\n\n  {data.get('hosted_link_url')}\n")
    print(f"link_token   {link_token}")
    print(f"expiration   {data.get('expiration')}")
    if ctx.settings.is_sandbox:
        print("credentials  user_good / pass_good")

    if not ctx.args.wait:
        print(f"\nthen claim it:\n  python -m plaid_lab claim {link_token}")
        return 0

    print(f"\npolling for a finished session (timeout {ctx.args.timeout}s)...")
    deadline = time.monotonic() + ctx.args.timeout
    while True:
        claimed = _claim(ctx, link_token)
        if claimed:
            for item in claimed:
                print(f"\nlinked {item.label()}")
                print(f"  products     {', '.join(item.products) or '(none)'}")
            return 0
        if time.monotonic() >= deadline:
            print(
                f"\ntimed out. The link_token is still valid until "
                f"{data.get('expiration')}; finish in the browser and run:\n"
                f"  python -m plaid_lab claim {link_token}",
                file=sys.stderr,
            )
            return 1
        time.sleep(ctx.args.poll)


def cmd_claim(ctx: Context) -> int:
    """Exchange the public_token(s) from a completed Link session."""
    claimed = _claim(ctx, ctx.args.link_token)
    if not claimed:
        print(
            "No finished session on that link_token yet. `link_sessions` stays "
            "absent until Link runs, so this means the browser flow is "
            "incomplete.",
            file=sys.stderr,
        )
        return 1
    for item in claimed:
        print(f"linked {item.label()}")
        print(f"  products     {', '.join(item.products) or '(none)'}")
        print(f"  stored in    {ctx.store.path}")
    return 0


def cmd_items(ctx: Context) -> int:
    """List locally stored Items for the current environment."""
    items = ctx.store.for_env(ctx.settings.env)
    rows = [
        [
            n,
            i.institution_name or i.institution_id,
            i.item_id,
            ",".join(i.products),
            i.created_at,
            "yes" if i.cursor else "no",
        ]
        for n, i in enumerate(items, 1)
    ]
    print(fmt.heading(f"items ({ctx.settings.env})"))
    print(
        fmt.table(
            rows, ["#", "institution", "item_id", "products", "created", "cursor"]
        )
    )
    return 0


def cmd_item(ctx: Context) -> int:
    """Item status from Plaid: products, webhook, and any pending error."""
    item = ctx.item()
    data = products.item_get(ctx.client, item.access_token)

    def render() -> None:
        body = data.get("item") or {}
        status = data.get("status") or {}
        print(fmt.heading(item.label()))
        for key in (
            "item_id",
            "institution_id",
            "institution_name",
            "webhook",
            "update_type",
            "consent_expiration_time",
        ):
            if key in body:
                print(f"{key:24} {body.get(key)}")
        print(f"{'available_products':24} {', '.join(body.get('available_products') or [])}")
        print(f"{'billed_products':24} {', '.join(body.get('billed_products') or [])}")
        error = body.get("error")
        print(f"{'error':24} {error.get('error_code') if error else 'none'}")
        last = (status.get("transactions") or {}).get("last_successful_update")
        if last:
            print(f"{'last tx update':24} {last}")

    ctx.emit(data, render)
    return 0


def _account_rows(accounts: list[dict[str, Any]]) -> list[list[Any]]:
    rows = []
    for account in accounts:
        balances = account.get("balances") or {}
        rows.append(
            [
                account.get("name"),
                account.get("mask"),
                f"{account.get('type')}/{account.get('subtype')}",
                fmt.money(balances.get("available")),
                fmt.money(balances.get("current")),
                fmt.money(balances.get("limit")),
                balances.get("iso_currency_code"),
            ]
        )
    return rows


ACCOUNT_HEADERS = ["name", "mask", "type", "available", "current", "limit", "ccy"]


def cmd_accounts(ctx: Context) -> int:
    """Accounts on the Item, with cached balances."""
    item = ctx.item()
    data = products.accounts_get(ctx.client, item.access_token)

    def render() -> None:
        print(fmt.heading(item.label()))
        print(fmt.table(_account_rows(data.get("accounts") or []), ACCOUNT_HEADERS))

    ctx.emit(data, render)
    return 0


def cmd_balances(ctx: Context) -> int:
    """Same as accounts, but forces a live balance refresh at the institution."""
    item = ctx.item()
    data = products.accounts_balance_get(ctx.client, item.access_token)

    def render() -> None:
        print(fmt.heading(f"{item.label()} (live refresh)"))
        print(fmt.table(_account_rows(data.get("accounts") or []), ACCOUNT_HEADERS))

    ctx.emit(data, render)
    return 0


def cmd_auth(ctx: Context) -> int:
    """ACH account and routing numbers."""
    item = ctx.item()
    data = products.auth_get(ctx.client, item.access_token)

    def render() -> None:
        names = {a["account_id"]: a.get("name") for a in data.get("accounts") or []}
        rows = [
            [
                names.get(entry.get("account_id"), entry.get("account_id")),
                entry.get("routing"),
                entry.get("wire_routing"),
                entry.get("account"),
            ]
            for entry in (data.get("numbers") or {}).get("ach") or []
        ]
        print(fmt.heading(f"{item.label()} ACH numbers"))
        print(fmt.table(rows, ["account", "routing", "wire_routing", "number"]))

    ctx.emit(data, render)
    return 0


def cmd_identity(ctx: Context) -> int:
    """Account holder details as the institution reports them."""
    item = ctx.item()
    data = products.identity_get(ctx.client, item.access_token)

    def render() -> None:
        print(fmt.heading(f"{item.label()} identity"))
        for account in data.get("accounts") or []:
            print(f"\n{account.get('name')} ({account.get('mask')})")
            for owner in account.get("owners") or []:
                print(f"  names      {', '.join(owner.get('names') or [])}")
                print(
                    "  emails     "
                    + ", ".join(
                        e.get("data") for e in owner.get("emails") or [] if e.get("data")
                    )
                )
                print(
                    "  phones     "
                    + ", ".join(
                        p.get("data")
                        for p in owner.get("phone_numbers") or []
                        if p.get("data")
                    )
                )
                for address in owner.get("addresses") or []:
                    parts = address.get("data") or {}
                    print(
                        "  address    "
                        + ", ".join(
                            str(parts.get(k))
                            for k in ("street", "city", "region", "postal_code")
                            if parts.get(k)
                        )
                    )

    ctx.emit(data, render)
    return 0


def cmd_transactions(ctx: Context) -> int:
    """Pull transactions via /transactions/sync and persist the cursor.

    Positive `amount` means money leaving the account -- Plaid's sign convention
    is the opposite of most ledgers.
    """
    item = ctx.item()
    cursor = None if ctx.args.reset else item.cursor
    data = products.transactions_sync(
        ctx.client,
        item.access_token,
        cursor=cursor,
        days_requested=ctx.args.days_requested,
    )
    ctx.store.update(item, cursor=data["next_cursor"])

    def render() -> None:
        names = {a["account_id"]: a.get("name") for a in data.get("accounts") or []}
        added = data["added"]
        shown = added[: ctx.args.limit] if ctx.args.limit else added
        rows = [
            [
                tx.get("date"),
                (tx.get("name") or "")[:38],
                fmt.money(tx.get("amount")),
                (tx.get("personal_finance_category") or {}).get("primary", ""),
                names.get(tx.get("account_id"), "")[:20],
                "pending" if tx.get("pending") else "",
            ]
            for tx in shown
        ]
        print(fmt.heading(f"{item.label()} transactions"))
        print(
            f"added {len(added)}  modified {len(data['modified'])}  "
            f"removed {len(data['removed'])}  pages {data['pages']}"
        )
        print("(positive amount = money out)\n")
        print(
            fmt.table(rows, ["date", "name", "amount", "category", "account", "state"])
        )
        if ctx.args.limit and len(added) > ctx.args.limit:
            print(f"\n... {len(added) - ctx.args.limit} more (use --limit 0 for all)")

    ctx.emit(data, render)
    return 0


def cmd_investments(ctx: Context) -> int:
    """Holdings, joined to the securities they reference."""
    item = ctx.item()
    data = products.investments_holdings_get(ctx.client, item.access_token)

    def render() -> None:
        securities = {s["security_id"]: s for s in data.get("securities") or []}
        accounts = {a["account_id"]: a.get("name") for a in data.get("accounts") or []}
        rows = []
        for holding in data.get("holdings") or []:
            security = securities.get(holding.get("security_id"), {})
            rows.append(
                [
                    accounts.get(holding.get("account_id"), "")[:20],
                    security.get("ticker_symbol") or security.get("name", "")[:20],
                    security.get("type"),
                    holding.get("quantity"),
                    fmt.money(holding.get("institution_price")),
                    fmt.money(holding.get("institution_value")),
                    fmt.money(holding.get("cost_basis")),
                ]
            )
        print(fmt.heading(f"{item.label()} holdings"))
        print(
            fmt.table(
                rows,
                ["account", "security", "type", "qty", "price", "value", "cost_basis"],
            )
        )

    ctx.emit(data, render)
    return 0


def cmd_investment_transactions(ctx: Context) -> int:
    """Buys, sells, dividends and fees over a date window."""
    item = ctx.item()
    end = dt.date.today()
    start = end - dt.timedelta(days=ctx.args.days)
    data = products.investments_transactions_get(
        ctx.client, item.access_token, start_date=start, end_date=end
    )

    def render() -> None:
        securities = {s["security_id"]: s for s in data.get("securities") or []}
        all_tx = data.get("investment_transactions") or []
        shown = all_tx[: ctx.args.limit] if ctx.args.limit else all_tx
        rows = [
            [
                tx.get("date"),
                tx.get("type"),
                tx.get("subtype"),
                (securities.get(tx.get("security_id")) or {}).get("ticker_symbol"),
                tx.get("quantity"),
                fmt.money(tx.get("price")),
                fmt.money(tx.get("amount")),
                fmt.money(tx.get("fees")),
            ]
            for tx in shown
        ]
        print(fmt.heading(f"{item.label()} investment transactions {start}..{end}"))
        print(f"{len(all_tx)} transactions over {data['pages']} pages\n")
        print(
            fmt.table(
                rows,
                ["date", "type", "subtype", "ticker", "qty", "price", "amount", "fees"],
            )
        )
        if ctx.args.limit and len(all_tx) > ctx.args.limit:
            print(f"\n... {len(all_tx) - ctx.args.limit} more (use --limit 0 for all)")

    ctx.emit(data, render)
    return 0


def cmd_liabilities(ctx: Context) -> int:
    """Credit card, student loan and mortgage terms."""
    item = ctx.item()
    data = products.liabilities_get(ctx.client, item.access_token)

    def render() -> None:
        accounts = {a["account_id"]: a.get("name") for a in data.get("accounts") or []}
        liabilities = data.get("liabilities") or {}

        credit_rows = []
        for card in liabilities.get("credit") or []:
            for apr in card.get("aprs") or []:
                credit_rows.append(
                    [
                        accounts.get(card.get("account_id"), "")[:24],
                        apr.get("apr_type"),
                        apr.get("apr_percentage"),
                        fmt.money(apr.get("balance_subject_to_apr")),
                        fmt.money(card.get("last_payment_amount")),
                        card.get("next_payment_due_date"),
                    ]
                )
        print(fmt.heading(f"{item.label()} credit"))
        print(
            fmt.table(
                credit_rows,
                ["account", "apr_type", "apr", "balance", "last_payment", "due"],
            )
        )

        loan_rows = [
            [
                accounts.get(loan.get("account_id"), "")[:24],
                loan.get("loan_name") or loan.get("account_number"),
                loan.get("interest_rate_percentage"),
                fmt.money(loan.get("minimum_payment_amount")),
                loan.get("next_payment_due_date"),
                fmt.money(loan.get("origination_principal_amount")),
            ]
            for loan in liabilities.get("student") or []
        ]
        print(fmt.heading("student loans"))
        print(
            fmt.table(
                loan_rows,
                ["account", "loan", "rate", "min_payment", "due", "original"],
            )
        )

        mortgage_rows = [
            [
                accounts.get(m.get("account_id"), "")[:24],
                m.get("interest_rate", {}).get("percentage"),
                m.get("interest_rate", {}).get("type"),
                fmt.money(m.get("next_monthly_payment")),
                m.get("next_payment_due_date"),
                fmt.money(m.get("origination_principal_amount")),
            ]
            for m in liabilities.get("mortgage") or []
        ]
        print(fmt.heading("mortgages"))
        print(
            fmt.table(
                mortgage_rows,
                ["account", "rate", "rate_type", "payment", "due", "original"],
            )
        )

    ctx.emit(data, render)
    return 0


def cmd_institution(ctx: Context) -> int:
    """Details for the Item's institution."""
    item = ctx.item()
    if not item.institution_id:
        print("Item has no stored institution_id", file=sys.stderr)
        return 1
    data = products.institutions_get_by_id(ctx.client, item.institution_id)

    def render() -> None:
        inst = data.get("institution") or {}
        print(fmt.heading(inst.get("name") or item.institution_id))
        print(f"institution_id  {inst.get('institution_id')}")
        print(f"products        {', '.join(inst.get('products') or [])}")
        print(f"country_codes   {', '.join(inst.get('country_codes') or [])}")
        print(f"oauth           {inst.get('oauth')}")
        print(f"status          {inst.get('status')}")

    ctx.emit(data, render)
    return 0


def cmd_reset_login(ctx: Context) -> int:
    """Put the Item into ITEM_LOGIN_REQUIRED to exercise the update path."""
    ctx.require_sandbox("reset-login")
    item = ctx.item()
    data = products.sandbox_item_reset_login(ctx.client, item.access_token)
    ctx.emit(data, lambda: print(f"reset {item.label()} -> ITEM_LOGIN_REQUIRED"))
    return 0


def cmd_fire_webhook(ctx: Context) -> int:
    """Fire a webhook at the URL configured on the Item."""
    ctx.require_sandbox("fire-webhook")
    item = ctx.item()
    data = products.sandbox_item_fire_webhook(
        ctx.client, item.access_token, ctx.args.type, ctx.args.code
    )
    ctx.emit(
        data,
        lambda: print(f"fired {ctx.args.type}/{ctx.args.code} for {item.label()}"),
    )
    return 0


def cmd_remove(ctx: Context) -> int:
    """Delete the Item at Plaid and drop it from the local store."""
    item = ctx.item()
    data = products.item_remove(ctx.client, item.access_token)
    ctx.store.remove(item)
    ctx.emit(data, lambda: print(f"removed {item.label()}"))
    return 0


# --- argument parsing ------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plaid_lab", description="Poke at the Plaid API."
    )
    parser.add_argument(
        "--json", action="store_true", help="print the raw API response"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Repeated on every subcommand as well as globally, so both
    # `--json transactions` and `transactions --json` work. argparse otherwise
    # accepts the global flag only before the subcommand name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    def add(name: str, func, help_text: str, item: bool = False):
        sub = subparsers.add_parser(
            name, help=help_text, description=help_text, parents=[common]
        )
        sub.set_defaults(func=func)
        if item:
            sub.add_argument("--item", help="index, item_id prefix, or institution")
        return sub

    add("env", cmd_env, "Show config and verify the API keys authenticate.")

    sec = add(
        "secrets", cmd_secrets, "Manage credentials in the OS credential store."
    )
    sec.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "migrate", "set", "clear"],
    )
    sec.add_argument(
        "name",
        nargs="?",
        default="secret",
        choices=["client_id", "secret"],
        help="for `set`: which credential",
    )
    sec.add_argument(
        "--env",
        choices=["sandbox", "production"],
        help="which environment's secret (defaults to PLAID_ENV)",
    )

    institutions = add(
        "institutions", cmd_institutions, "List institutions in this environment."
    )
    institutions.add_argument("--count", type=int, default=20)
    institutions.add_argument("--country", nargs="+", default=["US"])

    link = add("link", cmd_link, "Create a Sandbox Item, bypassing the Link UI.")
    link.add_argument("--institution", default=products.DEFAULT_INSTITUTION)
    link.add_argument(
        "--products",
        nargs="+",
        default=["transactions"],
        help="initial products, e.g. transactions auth identity investments",
    )
    link.add_argument(
        "--webhook",
        help="webhook URL; must be set at creation for fire-webhook to work",
    )

    import_token = add(
        "import-token",
        cmd_import_token,
        "Adopt an access_token created elsewhere into the local store.",
    )
    import_token.add_argument("token", help="an access_token")

    link_token = add(
        "link-token", cmd_link_token, "Create a link_token for the real Link UI."
    )
    link_token.add_argument("--user", default="plaid-lab-user")
    link_token.add_argument("--products", nargs="+", default=["transactions"])
    link_token.add_argument("--country", nargs="+", default=["US"])
    link_token.add_argument("--webhook")

    hosted = add(
        "hosted-link",
        cmd_hosted_link,
        "Create a Hosted Link session; Plaid hosts the page, no frontend needed.",
    )
    hosted.add_argument("--user", default="plaid-lab-user")
    hosted.add_argument("--products", nargs="+", default=["transactions"])
    hosted.add_argument("--country", nargs="+", default=["US"])
    hosted.add_argument("--webhook")
    hosted.add_argument(
        "--lifetime", type=int, help="URL lifetime in seconds (default: Plaid's)"
    )
    hosted.add_argument(
        "--wait", action="store_true", help="poll until the session finishes"
    )
    hosted.add_argument("--timeout", type=int, default=300)
    hosted.add_argument("--poll", type=int, default=3)

    claim = add(
        "claim", cmd_claim, "Exchange the public_token from a finished session."
    )
    claim.add_argument("link_token")

    add("items", cmd_items, "List locally stored Items.")
    add("item", cmd_item, "Item status, products and pending errors.", item=True)
    add("accounts", cmd_accounts, "Accounts with cached balances.", item=True)
    add("balances", cmd_balances, "Accounts with a live balance refresh.", item=True)
    add("auth", cmd_auth, "ACH account and routing numbers.", item=True)
    add("identity", cmd_identity, "Account holder name, email, phone, address.", item=True)

    transactions = add(
        "transactions", cmd_transactions, "Sync transactions and store the cursor.",
        item=True,
    )
    transactions.add_argument(
        "--reset", action="store_true", help="ignore the stored cursor and resync"
    )
    transactions.add_argument(
        "--limit", type=int, default=25, help="rows to display; 0 for all"
    )
    transactions.add_argument(
        "--days-requested",
        type=int,
        default=products.MAX_DAYS_REQUESTED,
        help=(
            "history to request, 1-730. Only applies on an Item's FIRST sync "
            "and cannot be widened later without re-linking"
        ),
    )

    add("investments", cmd_investments, "Investment holdings.", item=True)

    inv_tx = add(
        "investment-transactions",
        cmd_investment_transactions,
        "Investment transactions over a window.",
        item=True,
    )
    inv_tx.add_argument("--days", type=int, default=365)
    inv_tx.add_argument(
        "--limit", type=int, default=25, help="rows to display; 0 for all"
    )

    add("liabilities", cmd_liabilities, "Credit, student loan and mortgage terms.", item=True)
    add("institution", cmd_institution, "Details for the Item's institution.", item=True)

    add(
        "reset-login",
        cmd_reset_login,
        "Sandbox: force ITEM_LOGIN_REQUIRED.",
        item=True,
    )
    fire = add("fire-webhook", cmd_fire_webhook, "Sandbox: fire a webhook.", item=True)
    fire.add_argument("--type", default="TRANSACTIONS")
    fire.add_argument("--code", default="DEFAULT_UPDATE")

    add("remove", cmd_remove, "Delete the Item at Plaid and locally.", item=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = Context(args)
    try:
        return args.func(ctx)
    except PlaidError as exc:
        print(f"\nPlaid rejected the request:\n{exc.detail()}", file=sys.stderr)
        return 1
    except (ConfigError, StoreError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    except plaid.ApiValueError as exc:
        # Raised while BUILDING a request, before any call, so it never reaches
        # client.call() and would otherwise surface as a raw traceback.
        print(f"\nInvalid argument: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
