# plaid-lab

A Python client and CLI over the [Plaid API](https://plaid.com/docs/), aimed at
personal finance tooling.

## What this is

A personal finance application built and run by one individual developer, for
their own bank and brokerage accounts. It is not a commercial product, has no
customers, and is not distributed — the author is the only user and the only
account holder whose data it touches.

**Scope is read-only.** It reads balances, transactions, investment holdings
and liability terms in order to categorize spending and track net worth across
accounts. It initiates no payments, transfers, or money movement of any kind,
and wraps none of Plaid's payment products.

**Data handling.** It runs locally, not as a hosted service. API credentials
live in environment variables and Plaid access tokens in a local file; both are
excluded from version control. No financial data is transmitted anywhere other
than between this machine and Plaid's API — there is no server, no database,
and no third party in the path.

**Products used:** Transactions, Auth, Identity, Investments, Liabilities,
Balance. Linking goes through Plaid Link (Hosted Link), so bank credentials are
entered into Plaid and are never seen by this application.

Currently pointed at the Sandbox environment.

## Setup

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
copy .env.example .env      # then fill in the keys
```

Get `client_id` and the **Sandbox** secret from
<https://dashboard.plaid.com/developers/keys>. Plaid issues a separate secret
per environment against the same `client_id`, so `PLAID_SECRET` has to be the
one matching `PLAID_ENV`.

Verify:

```powershell
.venv\Scripts\python.exe -m plaid_lab env
```

## Linking an Item

An **Item** is one set of credentials at one institution. Normally a user links
one through the Link UI in a browser; in Sandbox you can skip that entirely:

```powershell
.venv\Scripts\python.exe -m plaid_lab link --products transactions auth identity
```

That calls `/sandbox/public_token/create` and exchanges the result for an
`access_token`, which is written to `items.json` (gitignored). Plaid never
returns an `access_token` a second time, so losing that file means re-linking.

`--institution` selects a Sandbox bank; the default is `ins_109508`
(First Platypus Bank), which supports the widest product set and needs no
OAuth. The other test banks are `ins_109509` (First Gingham), `ins_109510`
(Tattersall — no investments, identity or liabilities), `ins_109511` (Tartan)
and `ins_109512` (Houndstooth — no auth). Note that `institutions` lists the
real ~10,000-institution catalog, not these; the test banks have to be named.

`--webhook <url>` attaches a webhook URL, which has to be set at creation for
`fire-webhook` to work later.

A token created somewhere else — the Plaid dashboard, the Quickstart, another
machine — works here as long as it was created under the same `client_id`. Add
it to the store with:

```powershell
.venv\Scripts\python.exe -m plaid_lab import-token access-sandbox-<uuid>
```

`import-token` reads `item_id`, institution and products off `/item/get`, so
the token is the only argument. Re-importing an Item preserves its stored
transactions cursor.

## Linking through a real browser (Hosted Link)

`link` only works in Sandbox. The path that also works in Production is Hosted
Link: Plaid hosts the Link page, so no frontend is needed.

```powershell
.venv\Scripts\python.exe -m plaid_lab hosted-link --products auth transactions --wait
```

It prints a `https://secure.plaid.com/hl/...` URL. Open it, complete the flow
(Sandbox credentials are `user_good` / `pass_good`), and `--wait` polls
`/link/token/get` until the session finishes, then exchanges the `public_token`
and stores the Item. Without `--wait` it prints a `claim` command to run
afterwards.

This is the only path here that reaches an `access_token` the way Production
requires — a real browser, with credentials typed into Plaid rather than into
your code.

## Commands

| Command | Endpoint | Notes |
|---|---|---|
| `env` | `/institutions/get` | config check + auth probe |
| `institutions` | `/institutions/get` | `--count`, `--country` |
| `link` | `/sandbox/public_token/create` + `/item/public_token/exchange` | Sandbox only; `--institution`, `--products`, `--webhook` |
| `import-token` | `/item/get` | adopt an `access_token` made elsewhere |
| `link-token` | `/link/token/create` | a bare `link_token`; needs your own Link frontend |
| `hosted-link` | `/link/token/create` with `hosted_link` | Plaid hosts the page; `--wait` polls to completion |
| `claim` | `/link/token/get` + `/item/public_token/exchange` | exchange a finished session's `public_token` |
| `items` | (local) | what is in `items.json` |
| `item` | `/item/get` | products, webhook, pending error |
| `accounts` | `/accounts/get` | cached balances |
| `balances` | `/accounts/balance/get` | forces a live pull |
| `auth` | `/auth/get` | ACH routing + account numbers |
| `identity` | `/identity/get` | holder name, email, phone, address |
| `transactions` | `/transactions/sync` | incremental; `--reset`, `--limit` |
| `investments` | `/investments/holdings/get` | holdings joined to securities |
| `investment-transactions` | `/investments/transactions/get` | paginated; `--days`, `--limit` |
| `liabilities` | `/liabilities/get` | APRs, loan and mortgage terms |
| `institution` | `/institutions/get_by_id` | for the Item's bank |
| `reset-login` | `/sandbox/item/reset_login` | forces `ITEM_LOGIN_REQUIRED` |
| `fire-webhook` | `/sandbox/item/fire_webhook` | `--type`, `--code` |
| `remove` | `/item/remove` | deletes at Plaid and locally |

Two flags apply broadly:

- `--json` prints the raw API response instead of a table. The tables are a
  convenience; the JSON is what you build against.
- `--item` selects an Item by index, `item_id` prefix, or institution name.
  With one Item linked it can be omitted; with several, omitting it lists them
  and stops rather than guessing.

## Notes

- **Transaction amounts are positive when money leaves the account.** Plaid's
  sign convention is the opposite of most ledgers.
- **`/transactions/sync` is stateful.** It returns only what changed since the
  stored cursor, so a second run legitimately returns nothing. `--reset`
  replays from the beginning.
- **`--days-requested` only applies on an Item's first sync**, and cannot be
  widened later without re-linking. It defaults to the 730-day maximum here
  rather than Plaid's 90-day default, because getting it wrong is not
  recoverable. The institution may return less than requested.
- **An empty first sync means "not ready", not "no transactions."** Plaid
  fetches in two phases and returns an empty delta while the historical pull is
  in flight. Sync again before concluding an account is empty.
- **`/investments/transactions/get` has no `has_more` flag** and caps a page at
  500. It reports the real count in `total_investment_transactions`, and a
  caller that ignores it gets a truncated list with no error. Both paginated
  wrappers walk every page; `--limit` only caps what is *displayed*.
- **`reset-login` is not cheaply reversible** — the Item stays in
  `ITEM_LOGIN_REQUIRED` until it goes through Link update mode. In Sandbox,
  `remove` and re-`link`. Use a throwaway Item.
- Sandbox login is the universal `user_good` / `pass_good`. `link` never sees
  it, since it bypasses the credential prompt.
- Products have to be requested when the Item is created. Asking for
  `investments` data on an Item linked with only `transactions` returns an
  error, not an empty result.

## Layout

```
plaid_lab/
  config.py    credentials and environment resolution
  client.py    client construction, error normalization, JSON coercion
  products.py  one function per endpoint, plain values in and out
  store.py     access_token and transaction-cursor persistence
  fmt.py       console tables
  cli.py       argparse front end
```

Nothing outside `products.py` imports from `plaid.model.*`, so a library
version bump lands in one file.
