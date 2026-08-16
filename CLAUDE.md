# CLAUDE.md — plaid workspace

Guidance for Claude Code sessions run from `C:\Users\danim\plaid`.

## What this is (2026-08-15)

Personal tooling over the **Plaid API**, laid out like the Kalshi and Schwab
repos: one venv at the root, a `plaid_lab` package holding the client, and an
argparse CLI on top of it. Built on `plaid-python` (Plaid's own client).

**Long-term goal:** a custom personal-finances app. **Current goal:** Sandbox
only. Nothing here touches real bank data yet.

See `README.md` for setup and the command table. This file is about how to work
in the repo.

## State of play (2026-08-15)

Scaffold complete: 18 CLI commands wrapping 17 endpoints, venv built,
`plaid-python` 42.0.0 installed on Python 3.14.5.

**All 18 commands have returned live Sandbox data.** Every response-shape
assumption in `cli.py`'s render functions is confirmed, not assumed —
`accounts`, `auth`, `identity`, `transactions`, `investments`,
`investment-transactions`, `liabilities`, `item`, `institution`,
`institutions`, `link`, `link-token`, `items`, `balances`, `reset-login`,
`fire-webhook`, `remove`, `env` all ran against `ins_109508` / `ins_109509`.

Also exercised: the Item-selection fail-closed path (2 Items linked, no
`--item` — lists and stops), selection by institution substring, incremental
`/transactions/sync` (second run returns 0 added, correctly), and `PlaidError`
surfacing a real `ITEM_LOGIN_REQUIRED` 400.

One Item is currently linked: First Platypus Bank with
`transactions,auth,identity,investments,liabilities`.

**Bug found and fixed on the first live pass:** `/investments/transactions/get`
was silently truncating at 100 rows of 1170. See the pagination note below.

## Concepts worth not re-deriving

- **Item** = one set of credentials at one institution. It holds one or more
  **accounts**. The `access_token` is per Item, not per account.
- **`access_token` is returned exactly once**, by
  `/item/public_token/exchange`. It is stored in `items.json` (gitignored) and
  cannot be re-fetched. Losing that file means re-linking.
- **Products are chosen at link time.** An Item linked with `transactions` only
  will return an error, not empty data, from `/investments/holdings/get`. Link
  with the products you intend to call.
- **Sandbox can skip Link entirely** via `/sandbox/public_token/create`. Plaid
  recommends this over driving the Link UI in tests. Production has no
  equivalent — there the browser flow is mandatory.
- **The API is versioned by date**, pinned here to `2020-09-14` in
  `config.py`. Response shapes differ across versions; do not let the dashboard
  default decide.
- **Plaid removed the Development environment in 2024.** Sandbox and Production
  are the only hosts, and each has its own secret against the same `client_id`.
  A mismatched pair returns `INVALID_API_KEYS`, which reads like a bad
  `client_id` and is not.

## API facts worth not rediscovering

- **Transaction `amount` is positive when money leaves the account.** Opposite
  of most ledgers. Getting this backwards silently inverts every total.
- **`/transactions/sync` is stateful and incremental.** It returns
  added/modified/removed since the cursor, plus `next_cursor`. The cursor lives
  on the Item in `items.json`. A second run returning nothing is correct
  behavior, not a failure. `--reset` replays from the start.
  `/transactions/get` is the legacy endpoint; don't reach for it.
- **The two paginated endpoints paginate differently, and one lies quietly.**
  - `/transactions/sync` sets `has_more`. Loop until it is false.
  - `/investments/transactions/get` has **no `has_more`**. It caps a page at
    500, defaults to 100, and puts the real count in
    `total_investment_transactions`. Ignoring that field gets you a silently
    truncated list that looks complete — verified 2026-08-15: 100 rows
    returned against a total of 1170, no flag, no error. Both wrappers in
    `products.py` now walk every page; anything new must check for this shape
    rather than assuming `has_more` exists.
- **`/sandbox/item/fire_webhook` needs a webhook URL set at Item creation.**
  Pass `link --webhook <url>`; it goes into
  `SandboxPublicTokenCreateRequestOptions`. There is no way to attach one to an
  existing Item outside update mode.
- **`/sandbox/item/reset_login` is not cheaply reversible.** The Item is stuck
  in `ITEM_LOGIN_REQUIRED` and every product call on it 400s until it goes
  through Link update mode. In Sandbox the practical fix is `remove` and
  re-`link`. Test it on a throwaway Item, not the one you are working with.
- **`/institutions/get` in Sandbox returns the real ~10,096-institution
  catalog, not the test banks.** The test banks are `ins_109508`-`ins_109512`
  and have to be named directly. `ins_109508` (First Platypus) and `ins_109511`
  (Tartan) carry the widest product sets; `ins_109510` (Tattersall) has no
  investments, identity or liabilities; `ins_109512` (Houndstooth) has no auth.
- **Sandbox fixture data is static and dated.** Liability due dates sit in
  2019-2020, and investment transactions repeat on a ~10-day cycle. Don't read
  a stale date as a bug, and don't build date logic that assumes the fixtures
  move.
- Sandbox data may not be ready immediately after linking — a fresh Item can
  return `PRODUCT_NOT_READY` on the first transactions call. Not seen in
  practice yet (2026-08-15: 48 transactions came back on the first sync,
  seconds after linking), but documented by Plaid; retry rather than debugging.
- Universal Sandbox credentials are `user_good` / `pass_good`. `link` never
  needs them since it bypasses the prompt.

## Conventions

- **Secrets never enter the repo.** `.env` and `items.json` are gitignored.
  `items.json` holds `access_token`s — in Production each is a live credential
  to a real bank account. Never print one, echo it into a log, or paste it into
  a message. `Settings.masked()` exists for this; use it instead of formatting
  credentials by hand.
- **Nothing outside `products.py` imports `plaid.model.*`.** One function per
  endpoint, plain Python values in and out, so a library bump lands in one file.
  New endpoints go there first, then get a CLI command.
- **Errors go through `PlaidError`.** It hoists `error_code` out of the JSON
  body that `plaid.ApiException` leaves as a string. Branch on `error_code`,
  never on the message text.
- **Item selection fails closed.** `ItemStore.select` refuses to guess when
  several Items match or none is named — same rule as the Schwab repo's account
  tails. Don't add a "default to the first" fallback.
- Console output is cp1252 on this machine, so em-dashes and box-drawing
  characters come back as `?`. `fmt.py` is ASCII-only on purpose; keep it that
  way.
- Match the surrounding style: module docstrings explain the protocol and the
  reason for a design choice, not just the mechanics.

## Posture

Sandbox only, and read-mostly. The write-ish endpoints wrapped so far are
`/item/remove` and the Sandbox simulators (`reset_login`, `fire_webhook`),
which affect only test Items.

**Before pointing this at Production**, the user has to say so explicitly. That
switch means real bank credentials flow through Link and real `access_token`s
land in `items.json`; it is not a config tweak to be made in passing. Plaid also
gates Production access behind an application review, so it cannot happen
silently.

Payments and money movement (Transfer, Payment Initiation) are out of scope and
unwrapped. Don't add them speculatively.
