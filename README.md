# OANDA for Open Meridian

Spot FX through [OANDA's v20 API](https://developer.oanda.com/rest-live-v20/introduction/)
for an Open Meridian deployment. The plugin holds three roles:

- **`connectivity`** (contract v23): it places the placements that name it at
  OANDA and reports what OANDA did with them: acknowledgements, rejections,
  fills, financing charges and each daily value-date roll. It also reports
  fills of orders taken in OANDA's own platform or app, on no placement.
- **`custody`**: it reads the account chosen on Connections, or the one its
  `account_id` setting names, as the custodian's view, covering the
  account, its sync status, a statement per read and its activity.

- **`data`** (from 0.5.0): it records OANDA's prices in the lake, of the
  lake's existing kinds, prices and quotes. `live`: each currency pair's
  best bid and ask with their liquidity, as a quote, and their mid, as a
  price of kind `mid`, from OANDA's pricing stream for a standing want, at
  most once a second a pair, and from a price read now for the latest; kept
  90 days by its default terms. `daily`: each pair's close, the mid of
  OANDA's daily candle, on its day ending at 17:00 New York (ruled
  2026-10-13: "later, in the same repository").

Version 0.5.0 (not released) is built on open-meridian 0.31.0, contract v26:
the `data` role, and the environment its session reached on its venue
catalogue, the second key of the agents' demo-only guard. Its whole
declaration -- its settings, pages and tools, the accounts it names by
OANDA's IDs, its storage and its catalogue -- and its documentation are in
[docs/](docs/), which a deployment shows on the plugin's Documentation tab.

OANDA is a trademark of its owner. This project is not affiliated with or
endorsed by OANDA. It connects to OANDA's published API with an access token
that the deployment's administrator supplies. Which broker a person may trade
with is between that person and the broker, and the API's terms are settled
by the deployment admin.

## Settings

| Setting | What it is |
|---|---|
| `environment` | The role-common setting (contract v26): `paper`, labelled Practice, OANDA's practice host, or `live`. Practice until a person chooses Live. A value saved before 0.5.0, `practice`, still reaches the practice host, but the agents' guard reads it as live until it is saved again; Connections says so. |
| `token` | Secret. The personal access token made in OANDA's hub. It is never shown, logged or kept. OANDA does not keep it either: if it is lost, revoke it and make a new one. |
| `account_id` | Optional. Leave it empty and choose the account on Connections, from the v20 accounts the token reaches. Given, for example `101-001-1234567-001`, it is the account, as before 0.3.0, and Connections offers no choice. |
| `poll_seconds` | How often the account is read (custody). |
| `stale_after_hours` | How old the last good read may be, while OANDA does not answer, before the account is reported stale. |
| `sample_data` | Developer setting. Trades with, reads and takes prices from a synthetic OANDA built into the plugin, so you can develop before any token exists. Nothing reaches OANDA. |

The token, the account and the environment serve all three roles.

The SDK also declares a window setting and a past-the-window setting for each
kind of raw record: `activity` (seven years), `responses` (30 days),
`trading` (seven years) and, from 0.5.0, `pricing` (a week), what OANDA's
pricing answered the data role.

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
   admin on both `custody` and `connectivity`, the roles that trade and read the
   account. Agents use `read_connections` and
   `choose_account` (checked by `check_choose_account`). Who chose which
   account, and when, is kept in the plugin's storage.
4. On Account links, link the OANDA account to one of the deployment's
   accounts.
5. To trade, an admin of `connectivity` enables trading on the connection
   (below).

An `account_id` setting from before 0.3.0 keeps working: while it is set, it
is the account, and Connections lists the accounts without offering a
choice. Clear it to choose on Connections instead.

## The environment, and the agents' guard

The `environment` setting is one key of the agents' demo-only guard
(contract v26, G1): a connection is **Demo** only while the setting says
`paper` and the environment the session reached says `paper` too. The
plugin reads the second from the host it calls, `api-fxpractice.oanda.com`
`paper` and `api-fxtrade.oanda.com` `live`, and publishes it on its venue
catalogue (`VenueCatalogue.environment`). The synthetic OANDA reaches no
venue and reports `paper`. Anything else, a mismatch among it, counts as
live. Connections shows each connection's badge, Demo, Live, or "Live
(environment not confirmed)".

## Prices in the lake

- **A pair is two cash instruments**: a row's subject is the base
  currency's record, the one the lake asked for, its price a `Money` in the
  quote currency, as OANDA quotes it. Nothing is inverted or crossed, so
  the yen's own record is declined as not covered (OANDA quotes USD_JPY).
  Only OANDA's currency pairs are served.
- **Each currency** is resolved by its ISO 4217 code, a miss reported once;
  a subject is named by its record's `iso4217` identifier.
- **The venue** is OANDA, by its own name in the venue master; a venue not
  held is reported once and each row names it as reported.
- **Wants**: the latest of `live`, a price read now; a business date, a
  range or the latest week of `daily`. A standing want of `live` is kept
  current from the pricing stream, one stream for every pair kept current,
  opened again when that changes, after a drop waiting longer each time, a
  minute at most; one of `daily`, its forming day every five minutes.
  Declined `not_covered`, `beyond_history` (a past range of `live`: OANDA
  keeps no history of its quotes) or `source_silent`.
- **A restart** cannot read back a gap in `live`: OANDA keeps no quote
  history. The stream opens again from each pair's price now; a row is keyed
  by the pair and OANDA's own time, so one recorded before is unchanged.
- **Raw records**: each answer a row was read from is kept first, in the
  kind `pricing`, the SDK's raw store, a unit a UTC day; the stream's
  messages a row was read from as lines of the day's file per pair.
- **Not carried**: OANDA's closeout bid and ask, counted on the heartbeat.

## Trading

A new connection is **read-only**. An admin of the `connectivity` role
enables trading on it under Connections, "Enable trading". Until then, every
placement on it is rejected `unsupported`. The connection is the
`environment` together with the account (chosen on Connections, or named by
`account_id`), so changing either makes a new connection, which is
read-only again. Switching away from a connection also turns trading off
on it, whether by choosing another account on Connections or by changing
the `account_id` or `environment` setting. Returning to it finds it
read-only, and a person enables trading on it again: trading never comes
back on by itself. Each time this happens it is recorded in the plugin's
storage, with the change that caused it, who made it and when. Connections
and `read_connections` say so for the connection in use.

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
cancel, a pause, a stop and a restart. **On a controlled clock** (decision
66): before the order path, the e2e sets the synthetic OANDA's clock to the
coming Tuesday at 12:00 New York, so its fills trade on a Tuesday and its
roll is that day's 17:00, whatever day the e2e runs; the clock runs on from
there and is only ever set forward. The Makefile pins core v26's runtime
and harness as published (core 17a13a2, merged as 648e25f, chart 0.1.308);
`make e2e RUNTIME_IMAGE=... HARNESS_IMAGE=...` tries another.

**Safety:** tests and `make e2e` use the synthetic OANDA only. Any run on a
real practice account is the product owner's, done by hand.

## Developing

`make ci-local` runs lint, the tests, `meridian plugin check`, the image and
`make e2e`, all in containers. See AGENTS.md for the rest.
