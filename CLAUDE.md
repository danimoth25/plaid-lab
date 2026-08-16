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

Two Items are currently linked: First Platypus Bank
(`transactions,auth,identity,investments,liabilities`, 12 accounts) and Tartan
Bank (`auth,transactions`, 1 account) — the latter imported from a token the
user created outside this repo.

**An `access_token` is scoped to the `client_id`, not to the process or
machine.** A token minted in the Plaid dashboard or the Quickstart works here
unchanged; `import-token` adopts one by reading `item_id`, institution and
products off `/item/get`. Verified 2026-08-15. The converse — that another
developer's `client_id` cannot use your token — is documented but untested, and
testing it needs a second Plaid account.

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
- **`/link/token/create` returns a `link_token`, which is not a credential for
  any data.** It configures one Link session and nothing else. The ladder is
  `link_token` -> (browser) -> `public_token` -> `access_token`, and only the
  last one reads data. The dashboard's curl snippet for it looks like a token
  minting call and is not one; this confused the user once already.
- **Hosted Link removes the need for a frontend.** Passing `hosted_link` to
  `/link/token/create` adds `hosted_link_url` to the response — Plaid hosts the
  Link page at `https://secure.plaid.com/hl/<id>`. Verified 2026-08-15. For a
  single-user personal app this is the whole Production linking story; don't
  build a web frontend for it.
- **A Hosted Link session hands its result back through `/link/token/get`.**
  `link_sessions` is *absent* until a session actually runs, and a finished one
  carries the token at
  `link_sessions[].results.item_add_results[].public_token`. Polling that is an
  alternative to running a webhook receiver or a redirect URI, which a local
  script cannot host. `products.public_tokens_from_sessions` extracts them.
  Verified end to end 2026-08-15 through a real browser session.
- **One `link_token` can report several sessions, most of them junk.** A single
  browser run returned two: one finished with results, one with
  `finished_at: null`, no `results`, and only `OPEN`/`TRANSITION_VIEW` events.
  Iterate and select on the presence of `item_add_results`; never index
  `link_sessions[0]`.
- **The session result carries the selected accounts before the exchange** —
  `item_add_results[].accounts` has id, name, mask, type and subtype. Useful
  for showing the user what they linked without a follow-up `/accounts/get`.
- **`/item/public_token/exchange` is idempotent per `public_token`.**
  Exchanging the same one twice succeeds and returns the *same* `item_id`
  rather than erroring or creating a second Item. Verified 2026-08-15. Do not
  rely on an error to detect a double claim.
- **Sandbox Hosted Link runs a phone/OTP step.** The event trail was
  `OPEN, TRANSITION_VIEW, SUBMIT_PHONE, VERIFY_PHONE, SUBMIT_OTP,
  SELECT_INSTITUTION, SUBMIT_CREDENTIALS, HANDOFF`. It is not credentials-only,
  so a Link session takes ~25s of human time, not 5.
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
- **Account count reveals how an Item was made.**
  `/sandbox/public_token/create` always returns the institution's full fixture
  — 12 accounts on `ins_109508` and `ins_109509`. The user's dashboard-created
  Tartan Item has 1. A one-account Sandbox Item came through real Link with
  account selection, not through the sandbox endpoint.
- Sandbox data may not be ready immediately after linking — a fresh Item can
  return `PRODUCT_NOT_READY` on the first transactions call. Not seen in
  practice yet (2026-08-15: 48 transactions came back on the first sync,
  seconds after linking), but documented by Plaid; retry rather than debugging.
- Universal Sandbox credentials are `user_good` / `pass_good`. `link` never
  needs them since it bypasses the prompt.

## Conventions

- **Secrets live in the OS credential store, not on disk** (set 2026-08-15,
  before Production keys existed). `plaid_lab/secrets.py` wraps `keyring`,
  which resolves to `WinVaultKeyring` here — the Windows Credential Locker,
  DPAPI-encrypted against the logged-in account. Entries are `client_id`,
  `secret:<environment>` and `access_token:<item_id>`.
  - **`Item` has no `access_token` field.** It is a property backed by the
    credential store. `items.json` now holds only non-secret metadata —
    institution, products, cursor — and is safe to open in front of someone.
    Do not add the token back to the dataclass.
  - Resolution order is **keyring first, `.env` fallback**, so a fresh checkout
    still works and migration is a no-op switch. `.env` is for `PLAID_ENV`,
    which is configuration, not a secret.
  - `secrets migrate` moves values in but **deliberately does not edit `.env`**
    — a botched rewrite of the only copy of a credential is unrecoverable, so
    that deletion stays manual.
  - `secrets set` reads with `getpass`, never from argv, because a credential
    on the command line lands in shell history and the process list.
  - **What this buys:** protection against file theft, which is the realistic
    threat — `.env` is a filename infostealers target by pattern. **What it
    does not buy:** protection against code running as this user, which can
    call the same API. No scheme that decrypts unattended does better. Do not
    describe this as solving the problem.
- **Never print, log, or echo a credential.** `Settings.masked()` exists for
  this; use it rather than formatting credentials by hand.
- **Do the work through the CLI, not through ad-hoc `python -c` scripts.** Set
  by the user 2026-08-16 after a Production link was driven with inline scripts
  that read `item.access_token` and passed it around. Nothing leaked — what got
  echoed were `link_token`s and `hosted_link_url`s, not credentials — but the
  method put live tokens one stray `print` or traceback away from the session
  transcript, and a Sandbox `access_token` had already been pasted into a
  script earlier the same session.
  - Linking and claiming go through `hosted-link` / `claim` / `relink`. Do not
    hand-roll `link_token_create` calls.
  - If a question cannot be answered with an existing command, **add a
    command** rather than scripting around the library. `history` exists
    because "how far back does this Item reach" kept being answered with a
    throwaway script.
  - Reporting commands should emit aggregates — counts, dates, field presence —
    rather than transaction descriptions, amounts or balances, so their output
    is safe to paste. `history` follows this; `accounts` and `balances` do not,
    by design, so prefer `history` when the question is about coverage.
- **Nothing outside `products.py` imports `plaid.model.*`.** One function per
  endpoint, plain Python values in and out, so a library bump lands in one file.
  New endpoints go there first, then get a CLI command.
- **Errors go through `PlaidError`.** It hoists `error_code` out of the JSON
  body that `plaid.ApiException` leaves as a string. Branch on `error_code`,
  never on the message text.
- **Item selection fails closed.** `ItemStore.select` refuses to guess when
  several Items match or none is named — same rule as the Schwab repo's account
  tails. Don't add a "default to the first" fallback.
- **`ItemStore.add` merges rather than replaces, and that is load-bearing.**
  Re-adding an existing Item preserves its transactions cursor and
  `created_at`. Without it, re-importing a token or claiming a Link session
  twice silently resets the cursor and the next sync replays the Item's entire
  history as `added`. This was a real bug: the guard existed at the
  `import-token` call site and was forgotten on the claim path, so it now lives
  in the store where no caller can miss it. Don't move it back out.
- Console output is cp1252 on this machine, so em-dashes and box-drawing
  characters come back as `?`. `fmt.py` is ASCII-only on purpose; keep it that
  way.
- Match the surrounding style: module docstrings explain the protocol and the
  reason for a design choice, not just the mechanics.

## Production, and Capital One specifically (2026-08-15)

The user's primary bank is **Capital One (`ins_128026`, `oauth: true`)**, so its
constraints drive the design. A Production application is in progress; the
project URL given on the form is <https://github.com/danimoth25/plaid-lab>.

**Capital One is not an approval gate.** Plaid's OAuth guide lists per-bank
extra requirements, and Capital One is not among the banks needing anything
beyond the standard registration. Chase and PNC require the Security
Questionnaire (waived on Trial); Schwab can take six weeks. Capital One
requires none of that, and Plaid states most institutions open within hours of
the Dashboard registration being complete (Application Display Information,
Company Information, MSA, Security Questionnaire).

What Capital One *does* impose is operational, and each item below is a real
constraint on this code:

- **Consent refresh every 12 months.** The Item breaks annually and needs Link
  **update mode** to recover. Wired 2026-08-15 as `relink`, which combines
  update mode with Hosted Link so no frontend is involved. Key properties:
  - Passing `access_token` to `/link/token/create` is what selects update mode.
    **`products` must be omitted** — Plaid rejects a request carrying both,
    since the Item's products are already fixed.
  - **Update mode issues no new `access_token`.** The existing one resumes
    working, so there is nothing to claim afterwards and the stored cursor and
    history survive. Do not add an exchange step to this path.
  - `--wait` polls `/item/get` until `error` clears, since there is no token
    handoff to poll for.
- **No pending transaction data at all.** The pending -> posted transition that
  normally requires de-duplication via `pending_transaction_id` simply does not
  occur here. Transactions appear only once posted, so they land later than a
  Capital One user expects from the app UI.
- **The documented `min_last_updated_datetime` requirement did not
  materialize.** Plaid's OAuth guide says Capital One "cannot use
  `/accounts/balance/get` without specifying freshness requirements for
  non-depository accounts". Tested live 2026-08-16 against the real Item, both
  with and without the option: **both succeeded**, returning all 5 accounts and
  identical data. Do not treat it as a blocker. It is still wired as
  `balances --max-age <hours>` (default 6, sent as an aware UTC datetime —
  Plaid rejects a naive one) because it is harmless and may matter for a
  genuinely forced refresh, which is unverifiable here since
  `last_updated_datetime` comes back null.
- **`/transactions/refresh` errors on credit-card-only Items**, and **Identity
  is unsupported** on them.
- **Past-due credit cards cannot be linked at all.**
- **A company name change** requires notifying the Plaid account manager, and
  Items enter `ITEM_LOGIN_REQUIRED` afterwards.

Other banks worth knowing before wiring more Items: American Express, Citibank,
Fidelity, Navy Federal, PNC and TD all refresh consent annually; USAA every 18
months; Brex every 3. Bank of America is migrating APIs through 2026 — listen
for `PENDING_DISCONNECT`. Robinhood cannot add products after Item creation
unless they were consented up front via `additional_consented_products`, which
is also unwired here.

The general lesson: **annual consent expiry is the norm, not the exception.**
Any personal-finance app that intends to keep running needs update mode, and
`consent_expiration_time` on `/item/get` is the field that tells it when.

## Where this is going (set by the user 2026-08-15)

Two consumers, both **on-demand**. Nothing runs unattended:

1. **A local dashboard** the user opens.
2. **An MCP wrapper**, following `kalshi_mcp` / `schwab_mcp` — drive the data
   conversationally through Claude Code.

Origin: the user was getting good results dumping CSV exports into Claude chat
to build a personal budget and accounts spreadsheet, and wants the programmatic
version of that. The context of that chat is to be supplied when the budget
layer is actually built — **ask for it before designing that layer**, rather
than inventing a category scheme.

Do not assume scheduled or background operation. It has not been asked for, and
an earlier session asserted it unprompted and reasoned from it. Consequences of
it being on-demand:

- **Webhooks are worth nothing right now.** There is no always-on receiver and
  nothing to deliver to. `fire-webhook` stays as a Sandbox toy. Don't propose a
  webhook architecture.
- **Update mode does not need automating.** "Run this when it breaks" is
  sufficient, because a human is present at every call. It still has to
  *exist* — see the Capital One 12-month consent refresh.
- **The Schwab MCP may be the permanent Schwab path.** Its 7-day refresh token
  is a real cost only under unattended operation; when the user is already
  sitting there, re-login is free. Do not argue for routing Schwab through
  Plaid on maintenance grounds.

## History: `days_requested`, and the one-shot you cannot redo

**The user wants data from 2026-01-01 onward and nothing earlier** (their
current budget regimen starts there). That is ~227 days back as of
2026-08-15 — which is *more than Plaid's 90-day default* and far less than its
730-day maximum.

- **There are TWO `days_requested` settings and only one of them works.**
  This cost a real Production link on 2026-08-16.
  - `LinkTokenTransactions.days_requested`, passed to **`/link/token/create`**,
    is the one that governs history depth. Transactions are initialized when
    the Item is created, so this is the only moment the window can be set.
    Wired as `hosted-link --days-requested`, default 730.
  - `TransactionsSyncRequestOptions.days_requested`, passed to
    **`/transactions/sync`**, arrives after initialization has already
    happened and does nothing on an Item created through Link. It is still set
    (default 730) because it does apply to Items whose transactions were never
    initialized, but **do not rely on it**.
  - Evidence: a Capital One Item linked via `hosted-link` before the link-time
    setting existed returned **86 days** of history — the 90-day default —
    despite its first sync requesting 730.
- **Neither can be widened afterwards.** An Item initialized at 90 days is
  capped at 90 for its entire life; the only remedy is re-linking, which means
  a new Item and a new browser session.
- Range is 1-730, enforced client-side by the library. Production applies a
  30-day floor.
- **The Sandbox cannot validate lookback.** With 730 requested, `ins_109508`
  returned 48 transactions spanning 81 days (2026-05-23 to 2026-08-12). That is
  the fixture's size, not a limit — it says nothing about what Capital One will
  return. Treat real lookback as unknown until a Production Item exists.
- **Consequence for the CSV plan:** if Capital One honors ~227 days, Plaid
  alone covers 2026-01-01 forward and the historical CSV import is unnecessary.
  Keep it as a fallback, but check the oldest returned transaction on the first
  real sync before doing any CSV work. The clean start date is then a filter in
  the local store, not a data-sourcing problem.

**An empty first sync means "not ready", not "no transactions."** A freshly
linked `ins_109510` Item returned `added 0` on its first sync and 48 on the
next, seconds later — Plaid fetches in two phases (initial update, then
historical update) and sync returns an empty delta rather than
`PRODUCT_NOT_READY` while that is in flight. Earlier Items returned data
immediately, so this is timing-dependent and will not reproduce reliably. A
dashboard that syncs right after linking and renders the result will show an
empty account and look broken. Re-sync before concluding an Item has no data.

## Billing model, and which commands cost money

**Plaid publishes no price list.** Their billing doc says so directly: "A price
list is not available in the documentation." Rates appear only on the last page
of the **Production access request** flow, and otherwise come from sales. Do
not go looking for a public pricing page; there isn't one.

**The Trial Access flow is a different flow and shows no pricing** — it is KYC
and security agreements only. This account went through Trial Access, so **no
rates have ever been seen**. Don't assume otherwise or ask the user to recall a
number they were never shown.

**The Trial plan is perpetually free with no expiration**, bounded only by the
10-Item limit. So at the current scale this project costs nothing and the
opacity is irrelevant. Everything below is dormant until there is a reason to
leave the Trial.

On a paid plan it would matter, because **product choice at link time decides
the billing shape for the life of the Item**, and products are fixed at link
time:

| model | products | implication |
|---|---|---|
| **Subscription, monthly per Item** | Transactions, Recurring Transactions, **Liabilities**, Investments | recurring cost per linked Item |
| **One-time per Item** | Auth, Identity, Income, Layer | paid once at link |
| **Per-request flat fee** | **Balance**, Signal, **refresh endpoints**, Identity Match | every call costs |

Consequences for this code, on a paid plan:

- The live Capital One Item carries **two** subscription products
  (`transactions`, `liabilities`) and would bill monthly twice over.
  **Decision, 2026-08-16: leave it.** The user does not expect to use the
  statement and payment-due data, but on the Trial it is free, and dropping it
  means re-linking — which spends one of the 10 permanent Item slots to save
  nothing. Revisit only if leaving the Trial becomes necessary, and link
  future Items without `liabilities` unless there is a use for it.
- **`balances` is a per-request charge.** `accounts` is not — it returns cached
  balances from the Item. A dashboard should call `accounts` by default and
  `balances` only when the user explicitly asks for a fresh pull. Do not put
  `balances` on an auto-refresh.
- **`refresh` is a per-request charge** and was used freely during the
  Sandbox and investigation phases. Treat it as a deliberate user action, never
  something a dashboard does on load.
- Omitting `auth` avoided a one-time fee as well as the ACH-number exposure.
  The security argument and the cost argument point the same way.

## The Item budget — 10, and removal does not refund

**The Production free trial allows 10 Items, and `/item/remove` does NOT free
the slot.** Confirmed by the user against Plaid's documentation, 2026-08-16.
A deleted Item is gone but its slot stays spent.

This changes how Production work is done here:

- **Do not link an Item to test a hypothesis.** There is no undo. Two Capital
  One Items were burned proving a `days_requested` behaviour that was written
  down in Plaid's own docs the whole time. That is 2 of 10 slots, permanently,
  for an answer that cost one doc fetch.
- **Read the documentation before linking anything.** Not as general good
  practice — as the only way to avoid spending an irreversible resource.
- Sandbox has no such limit. Every behavioural question that Sandbox can answer
  belongs there. Link in Production only to reach data that is actually wanted.
- Before any new Production link, state which of the remaining slots it spends
  and why it cannot be answered in Sandbox or from docs.

Budget as of 2026-08-16: **2 of 10 spent**, 1 Item live (Capital One), 8
remaining. Expected future needs are modest — Schwab, and whatever other
institutions the user actually banks with — so 8 is comfortable, but only if no
more are spent on experiments.

## The live Capital One Item (linked 2026-08-16)

First Production link. `liabilities,transactions` — **no `auth`**, deliberately,
so the credential does not carry ACH routing and account numbers.

- **5 accounts**: 360 Checking, 360 Performance Savings, and three credit cards
  (Savor, VentureOne, Quicksilver).
- **Only 3 carried transactions** in the returned window; VentureOne and
  Quicksilver came back with zero. `accounts_get` lists all 5, so they are
  linked — most likely simply unused in the window rather than missing.
- **`consent_expiration_time` is 2027-08-16T03:20:03Z** — exactly 12 months,
  confirming Capital One's annual refresh with a concrete date. `relink` is the
  recovery path.
- **History came back as 86 days (oldest 2026-05-21), not the 730 requested**,
  because of the two-`days_requested` bug above.

### What Capital One does and does not return (verified live 2026-08-16)

Balances need **no product at link time** — `/accounts/balance/get` works on
this Item even though `billed_products` is only `liabilities,transactions`.
All 5 accounts return `balances.current`. But three fields are empty across the
board, and two of them matter:

| field | state | consequence |
|---|---|---|
| `balances.current` | present, all 5 | net worth works |
| `balances.available` | **null, all 5** — including checking and savings | no "available to spend" |
| `balances.limit` | **null, all 3 cards** | **no credit utilization** |
| `aprs` | **empty array, all 3 cards** | no interest modelling |
| `last_updated_datetime` | null | balance freshness unknowable |

`/liabilities/get` is otherwise healthy on all three cards: `is_overdue`,
`last_payment_date`, `last_payment_amount`, `last_statement_issue_date`,
`last_statement_balance`, `minimum_payment_amount` and `next_payment_due_date`
are all populated. So statement and payment tracking is fine; utilization and
APR modelling are not.

**`balance` cannot be requested as an initial product, and never needs to be.**
Plaid rejects it outright:

    INVALID_PRODUCT: balance is not a valid product for this field. It is
    automatically initialized when any other valid product is included.

So there is no permissions gate behind the nulls and nothing to enable — the
data is simply not supplied by Capital One. Do not propose adding `balance` to
a product list; the API will 400. The user has accepted the loss: they carry
zero or near-zero card balances, so utilization is not meaningful for them, and
credit limits can be filled in by hand later if a use for them appears.

**Settled, and it is documented — Capital One provides 90 days, full stop.**
Plaid's Transactions documentation states it outright:

> The user's institution may provide limited transaction history. For example,
> Capital One provides only 90 days of transaction history and does not provide
> pending transactions.

So the 86 days observed is the institution's published limit, not a bug, not a
tier, and not an unfinished historical update. **Do not investigate this
again.** Everything below was already tested and all of it is consistent with
the documented 90 days:

- `days_requested: 730` verified present in the link request.
- `/transactions/get` asked explicitly from 2026-01-01 — nothing older than
  2026-05-21.
- `/transactions/refresh` completed (`last_successful_update` advanced from
  03:48:13 to 03:56:50) and returned the same 86 days.
- A second Item linked with `days_requested=730` set correctly at link time
  returned coverage identical to one that took the 90-day default.

The same doc confirms the initialization rule the code already follows:

> this field will only take effect when Transactions is added to an Item for
> the first time; if you need more than 90 days ... you will need to delete the
> Item and create a new one.

**Process note.** This conclusion was reached, then retracted on a user recollection
that another app had pulled full history from the same login, then restored
when the user found the documentation. The retraction was reasonable on the
evidence at the time. The cheaper path was to read Plaid's Transactions doc
before running any of the four tests above — the answer was one fetch away.
See the standing rule about consulting reference material first.

**Consequence: the 2026-01-01 target is unreachable through Plaid.** The
historical CSV import is required, covering 2026-01-01 to roughly 2026-05-21.
Plaid owns everything from the seam forward. Keep `days_requested=730` at link
time anyway — it costs nothing and other institutions may honour more.

`transactions_sync`'s `accounts` array is **not** a reliable roster — Item 2's
sync omitted the Savor card while still returning its transactions. Build
account lists from `/accounts/get`. `cmd_history` did this wrong once and
under-summed its own table.

## Division of labor — set by the user 2026-08-16

**Plaid's job is "replace export and parse". That is all it was ever meant to
do.** No more downloading monthly CSVs, no more PayPal PDF parser.

**Categorization is the LLM's job, through the MCP wrapper.** That is a
deliberate reason the MCP is in scope, not an afterthought: the user is already
paying for a model that is good at this, so a hand-built classifier is not the
goal and should not be proposed as one.

An earlier session twice inflated the stated goal — assuming unattended cron
operation, then assuming Plaid should replace categorization. Neither was ever
asked for. **Take the stated scope literally.**

The refinement that follows, and the reason the store still matters: an LLM
categorizing fresh each month is *inconsistent*, not wrong. `full_merge.py`'s
keyword cascade is best read as a **record of decisions already made**, not as a
classifier to rebuild.

**Precedent has two shapes, and conflating them is the mistake to avoid**
(user's point, 2026-08-16). Merchant → category is not a function for every
merchant:

- **Deterministic** — `ROCKET MORTGAGE` → Mortgage, `LENDINGCLUB` → Debt,
  `HCTRA` → Auto - Tolls. One merchant, one answer, always. Record and apply.
- **Ambiguous** — Humble Bundle sells games, books and software; Amazon and
  Walmart sell anything. The right precedent is *not* a fixed category. It is
  "this merchant is ambiguous: assign a generic bucket, flag it, and ask what it
  was." The user's phrasing: "put in a generic category and ask later if it was
  games or books or general software."

The whole Amazon item-by-item list in `MANUAL_NOTES_AND_DESCRIPTIONS.md` exists
because Amazon is the extreme case of the second kind, and the Walmart 6/26
split is the same problem inside a single charge. So the store must be able to
record *ambiguity* and a pending question, not only a resolved category.

**Look the merchant up. Do not infer it from the name.** An ML classifier has to
guess from the string; a model with web search does not, and guessing anyway is
the wrong reflex. Both LunchMoney's classifier and a Claude Chat session
independently miscategorised `PAPABEN'S` as restaurant/dining, because "Papa
Ben's" reads as a diner. One search resolves it: Papa Ben's Train Place, a model
train hobby shop on Bellaire Blvd in Houston. The merchant string plus the
user's city was sufficient.

The lookup also returns **which shape of merchant it is**, which is the part the
name cannot give:

- a train store sells one kind of thing -> deterministic, record it, never ask
- Humble Bundle sells games, books and software -> the search surfaces the
  ambiguity, so it correctly resolves to "flag and ask" rather than to a
  confident wrong category

Limits: works for merchants with a web presence, which is most retail. It will
not help with PayPal's obscured descriptors or generic ACH strings — those still
need bank-side cross-referencing. Cost is one search per *new* merchant, since
the result becomes precedent.

`merchant_category_code` (the bank's own MCC) is a second prior sitting unused in
the Plaid payload — the Sandbox Uber row carried `4121`, and a hobby shop would
be `5945`. Whether Capital One populates it is **unverified**; check before
relying on it.

**This design is explicitly deferred.** Per the user: work out the precedent
model after the Plaid pipeline is up and running, not before. Do not build a
rules engine ahead of the data flowing.

## Next steps (as of 2026-08-16, end of session)

In order. Nothing here is started.

1. **Rebuild the master transaction dataset with descriptions carried through.**
   `data/all_spend_transactions.csv` is `Date,Category,Amount,Month,Source` — no
   merchant or description. That makes it unjoinable to Plaid, whose match key
   is `merchant_name` / `merchant_entity_id`. The raw statements in
   `provided_files/` still have descriptions, so this is a rebuild, not a
   recovery. Do this before designing any schema.
2. **Reconcile `full_merge.py` against `group_map.py`.** The cascade reflects an
   older taxonomy and cannot reproduce the current master CSV as written:
   - returns `Phone Bill`; taxonomy has `Phone & Internet`
   - returns `Video/Tabletop Gaming`; taxonomy splits `Video Games` /
     `Tabletop Gaming`, and that split was decided per merchant
   - returns `Hobby - Art`, which is **not in `group_map.py` at all**
3. **Design the local store**, schema falling out of the rules rather than the
   reverse. It must hold the decision record from the section above, and it is
   required regardless: `/transactions/sync` is a change feed and
   `cmd_transactions` currently discards what it consumes.
4. **Build the MCP wrapper** (`plaid_mcp`, following `kalshi_mcp` /
   `schwab_mcp`). This is where categorization actually happens.
5. **Import the historical CSV** covering 2026-01-01 to ~2026-05-21, since
   Capital One only serves 90 days. Note the seam is an **overlap**, not a gap —
   the workbook runs to 2026-08-14 and Plaid starts 2026-05-21, so roughly three
   months are described by both sources. Use the overlap to validate the
   pipeline, then cut on a hard date so nothing is counted twice.
6. **Consider a PayPal Item** (~71 rows, 15% of the dataset). It would retire
   `parse_paypal4.py` and the PDF pipeline. Costs one of 8 remaining slots.

Rules that must survive into the store, all of which a naive ingest gets wrong:

- Savings & Investing is shown separately and **never** inside Total Spending.
- Interest is excluded from **both** income and savings. Deliberate, reversed
  twice, final.
- Zelle deposits from a household member are reimbursement, **not income**.
- PayPal "Pay in 4" repayment legs are debt paydown, **not new spending**.
- Bank-side `PAYPAL` rows are excluded and replaced by PayPal statement detail.
  If PayPal is ever linked through Plaid, preserve that substitution or 71
  transactions double-count.
- Internal transfers are netted. **Plaid returns both legs of every transfer.**

## Data handling for the budget bundles

The bundles carry real statements, account numbers, balances, full merchant
history, and a household member's legal name (hardcoded in `full_merge.py` as an
exclusion rule). **This repo is public.** They arrived untracked and unignored
while `git add -A` was in routine use; nothing was committed, by timing alone.
`.gitignore` now covers `*.zip`, `*.csv`, `*.pdf`, `*.xlsx`, `*.xls`, `*.ofx`,
`*.qfx` and the `data/`, `budget/`, `provided_files/`, `statements/` directory
names, deliberately broader than the current files require.

Extract and work on these under the scratchpad, never inside the repo. Report
structure, counts and date ranges rather than amounts, merchants or names.

## The storage decision, which the sync model forces

**`/transactions/sync` is a change feed, and `cmd_transactions` currently
discards what it consumes.** It prints the deltas and advances the cursor, so
the data is unreachable on the next call without `--reset`. Already observable:
item 1 returned 48 transactions, then 0. A dashboard built on the current code
would open and show nothing.

So a local store is not a preference, it is required by the endpoint's
semantics. It is also where everything Plaid does not know about has to live:
budget categories, recategorizations, splits, notes, targets. Plaid's
`personal_finance_category` is a model output carrying a `confidence_level`
(the sandbox Uber row reads `LOW`) — it is a starting point to override, not
ground truth.

**The local store is the unification point, not Plaid's wire format.** Schwab
data from the MCP adapts into the same tables rather than being bent into
Plaid's response shape. This replaces an earlier suggestion in conversation
that aggregation should target the Plaid schema.

Nothing here is built yet. `store.py` persists Items and cursors only.

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

### What a stolen credential actually reaches

Do not call this stack "read-only" without qualification — an earlier session
did, and the user correctly pushed back. Three separate facts:

1. **Plaid's API is not read-only.** Transfer moves money by ACH; Payment
   Initiation does in the EU/UK. `transfer` even shows up in
   `available_products` on the Sandbox Items here.
2. **Those products require a separate Transfer Application and approval** —
   "complete the Transfer Application and receive approval" — so they are not
   reachable just because Production access exists. Confirm this account has
   not been onboarded to Transfer, and leave it that way.
3. **`auth` is the real escalation, and it needs no Plaid write endpoint at
   all.** `/auth/get` returns account and routing numbers (the Sandbox Item
   returns routing `011401533`, account `1111222233330000`). Anyone holding
   those can attempt an ACH debit through any processor, entirely outside
   Plaid.

So the honest statement is: read access **to data that includes the means to
move money elsewhere**, not "a read-only login".

**Therefore: link Production Items with the minimum product set.** A budget and
net-worth app needs `transactions`, `investments`, `liabilities` and `balance`.
It does **not** need `auth`, and requesting it enlarges the blast radius of a
stolen credential for no benefit. Products are fixed at link time, so this has
to be right on the first link — same one-shot problem as `days_requested`.
