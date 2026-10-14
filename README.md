# OANDA for Open Meridian

Spot FX through [OANDA's v20 API](https://developer.oanda.com/rest-live-v20/introduction/)
for an Open Meridian deployment. The plugin holds two roles:

- **`connectivity`** (contract v23): it places the placements that name it at
  OANDA and reports what OANDA did with them: acknowledgements, rejections,
  fills, financing charges and each daily value-date roll. It also reports
  fills of orders taken in OANDA's own platform or app, on no placement.
- **`custody`**: it reads the account chosen on Connections, or the one its
  `account_id` setting names, as the custodian's view, covering the
  account, its sync status, a statement per read and its activity.

A `data` role, for OANDA's bid/ask quotes and a 17:00 New York daily close,
is planned for a later version of this repository (ruled 2026-10-13).

OANDA is a trademark of its owner. This project is not affiliated with or
endorsed by OANDA. It connects to OANDA's published API with an access token
that the deployment's administrator supplies. Which broker a person may trade
with is between that person and the broker, and the API's terms are settled
by the deployment admin.

## Settings

| Setting | What it is |
|---|---|
| `environment` | `practice` or `live`: the OANDA host the account is on. Use practice first. |
| `token` | Secret. The personal access token made in OANDA's hub. It is never shown, logged or kept. OANDA does not keep it either: if it is lost, revoke it and make a new one. |
| `account_id` | Optional. Leave it empty and choose the account on Connections, from the v20 accounts the token reaches. Given, for example `101-001-1234567-001`, it is the account, as before 0.3.0, and Connections offers no choice. |
| `poll_seconds` | How often the account is read (custody). |
| `stale_after_hours` | How old the last good read may be, while OANDA does not answer, before the account is reported stale. |
| `sample_data` | Developer setting. Trades with and reads a synthetic OANDA built into the plugin, so you can develop before any token exists. Nothing reaches OANDA. |

The SDK also declares a window setting and a past-the-window setting for each
kind of raw record: `activity` (seven years), `responses` (30 days) and
`trading` (seven years).

## Setting it up

1. In OANDA's hub, make a personal access token for the account's login.
2. On the plugin's Settings, choose the `environment` (practice first) and
   enter the `token`. Leave `account_id` empty.
3. On Connections, under Account, the plugin lists the v20 accounts the
   token reaches (OANDA's `GET /v3/accounts`). A v1 account, whose ID is
   digits only, is listed as unusable, with the reason: this plugin speaks
   only the v20 API. Choose the account and press "Use this account". Where
   the token reaches exactly one usable account it is preselected, but
   nothing is read or traded until an admin confirms it. Choosing needs
   admin on both of the plugin's roles, `custody` and `connectivity`, as the
   `account_id` setting does. Agents use `read_connections` and
   `choose_account` (checked by `check_choose_account`). Who chose which
   account, and when, is kept in the plugin's storage.
4. On Account links, link the OANDA account to one of the deployment's
   accounts.
5. To trade, an admin of `connectivity` enables trading on the connection
   (below).

An `account_id` setting from before 0.3.0 keeps working: while it is set, it
is the account, and Connections lists the accounts without offering a
choice. Clear it to choose on Connections instead.

## Trading

A new connection is **read-only**. An admin of the `connectivity` role
enables trading on it under Connections, "Enable trading". Until then, every
placement on it is rejected `unsupported`. The connection is the
`environment` together with the account (chosen on Connections, or named by
`account_id`), so changing either makes a new connection, which is
read-only again.

- **An FX order** is an order in the base currency's cash instrument, with
  its limit or stop price in the quote currency. From contract v23 the order
  names its quote currency (`quote_currency`), so a market order names its
  pair too. The plugin finds the pair OANDA quotes in its pair map. It never turns a pair round: an order in
  USD priced in EUR is rejected, and the rejection names EUR_USD.
- **What is sent**: OANDA's signed units of the base currency, the
  placement's ID as the order's client extension ID, and market (FOK, IOC),
  limit or stop orders (GTC, GTD, day as OANDA's GFD, FOK, IOC).
- **Facts** come from the account's transaction stream. Every poll also
  reads the transactions since the last one taken, and that cursor is kept
  in storage.
- **A fill's value date** is the pair's spot date from the date read (core's
  `ComputeDate`). OANDA's fill states no value date. If the date read gives
  no answer, the fill waits and is asked about again on each poll. It is
  never sent with a guessed date.
- **The roll.** At each 17:00 New York OANDA rolls every open trade to the
  next spot date. The plugin reports each trade's financing as a charge of
  kind financing on the placement whose fill opened it. It then reports a
  value-date roll for each pair, moving the pending lines from their value
  date to the date read's answer for the roll's moment, linked to those
  charges. A fill reported after OANDA rolled it (one that waited for its
  value date, or an accepted fill from OANDA's app) gets a catch-up roll.
- **Commission** on a fill is a fee of kind commission.
- **Orders taken outside the platform**, in OANDA's app or by OANDA itself
  (a stop loss, a margin closeout), are never recorded as orders. Their
  fills in a currency pair are reported on no placement, so the desk's
  `ems` accepts them or answers them DK. Their financing is kept unplaced
  until an accept, then charged again on the placement the accept made.
  OANDA takes no execution acknowledgement, and the catalogue says so.
- **An unanswered send** is resolved by asking OANDA for the order by its
  client ID. It is never sent again.
- **Cancel and replace** are taken from the placement's change requests.
- **Pause** and **stop** are honoured. A stop cancels at OANDA at once.

## Custody

Each open trade is shown as two cash legs: its units of the base currency,
and the units times its price, negated, of the quote currency. Each leg is
pending to the value date the position now rolls to. The account's currency
also carries the balance, settled. This follows the rolling-spot ruling of
2026-10-13.

Activity is each transaction that moved cash, with these kinds:

- fills are purchases or sales;
- commissions are fees;
- a profit in a quote currency, converted to the account's currency, is a
  journal;
- each roll's financing is interest;
- funds moved in or out are contributions, withdrawals or transfers.

## Proven against a released runtime

`make e2e` runs the plugin on the synthetic OANDA, on the plugin harness that
the Makefile pins beside its core. It chooses the account on Connections,
the synthetic token's one usable account preselected and confirmed and its
v1 account listed unusable, links it through the plugin's own Account links
form, holds the street to `e2e/expected.street` and
`e2e/expected.activity`, and then runs the order path with a desk stand-in
(`e2e/desk.py`, `e2e/orders.py`): a limit and a market order filled and
pending to their value date, an order from OANDA's app reported on no
placement, a roll, the desk's accept and its catch-up roll, a replace, a
cancel, a pause, a stop and a restart. The order path needs contract v23's
core: until core v23 is published, run it as
`make e2e RUNTIME_IMAGE=... HARNESS_IMAGE=...` with a local build.

**Safety:** tests and `make e2e` use the synthetic OANDA only. Any run on a
real practice account is the product owner's, done by hand.

## Developing

`make ci-local` runs lint, the tests, `meridian plugin check`, the image and
`make e2e`, all in containers. See AGENTS.md for the rest.
