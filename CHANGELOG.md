# Changelog

## 0.5.0

On SDK 0.31.0 and contract v26, the v26 build's plugin step (plans/the-v26-build
§8): the `data` role, before the FX sample ships, and the environment the
session reached on the venue catalogue, for the agents' demo-only guard
(spec/the-contract-gaps-v26-and-v27-close, G1).

- **The `data` role** (`lake.py`), a third role beside `custody` and
  `connectivity` (ruled 2026-10-13: "later, in the same repository"), on the
  SDK's data-role helpers (`meridian.roles.data`: wants, standing wants,
  recording in the lake's batches, licences, the venue resolved once; the
  raw records in `meridian.edge.RawStore`). Two datasets, of the lake's
  existing kinds, prices and quotes:
  - `live`: each currency pair's best bid and ask with their liquidity, as a
    quote, and their mid, as a price of kind `mid`, from OANDA's pricing:
    read now for the latest, and from OANDA's pricing stream for a standing
    want, at most once a second a pair. A price OANDA says is not tradeable
    is `halted`. Kept by its default terms for 90 days, for TCA's look-back
    (spec/the-fx-and-multi-asset-sample-plugins §1.5), the deployment's to
    change.
  - `daily`: each pair's close, the mid of OANDA's daily candle, on its day
    ending at 17:00 New York; the forming day restated.
  A row's subject is the base currency's record, its price in the quote
  currency, as OANDA quotes the pair: never inverted or crossed. Declines:
  `not_covered`, `beyond_history` (a past range of `live`: OANDA keeps no
  quote history, so a restart's gap is not read back) and `source_silent`.
- **Its pages**: the data role's **Connection** (`/`, `read_connection`) and
  **Datasets** (`/datasets`, `read_datasets`), at admin, the SDK's records
  and templates for a venue listing pairs, Connection extended with the
  environment and the guard's badge. **Statements moves to `/statements`**.
- **The environment** is the role-common setting (`meridian.environment`,
  `demo="paper"`): `paper`, labelled Practice, or `live`, serving all three
  roles. A value saved before 0.5.0, `practice`, still reaches the practice
  host; the guard reads it as live until it is saved again, and Connections
  says so.
- **The session's reported environment** (contract v26, W11.20): read from
  OANDA's host, `api-fxpractice` `paper` and `api-fxtrade` `live`, published
  on the venue catalogue (`VenueCatalogue.environment`); the synthetic OANDA
  reports `paper`. Connections and Connection show each connection's badge,
  Demo, Live, or "Live (environment not confirmed)".
- **The raw-record kind `pricing`**, a week by default, moved past its window
  by the same pass as the other three; a pricing record is followed from a
  row's reference on Raw records. Not carried: OANDA's closeout bid and ask.
- **One stream** hears the lake's wants and the book's placements and
  instructions; placements heard before trading starts are left to its
  start's reads.
- **The suites**: the data suite (`tests/test_lake.py`), every case mapped or
  not presented with why; the v26 cases of the custody and connectivity
  suites pass as before.
- **The pins**: open-meridian 0.31.0 and plugin-python:0.31.0, core v26's
  runtime and harness (core 17a13a2, merged as 648e25f, chart 0.1.308, by tag
  and digest), and `meridian` 0.1.48 in `check.yaml`.

## 0.4.0 (2026-10-19)

On SDK 0.29.0 and contract v25 (sdk-contract/plugins-are-shared-and-
documented-contract), the v25 build's step 8; on top of 0.3.0, which merges
first.

- **The whole declaration** (`declaration.py`): its settings, its interface
  -- Connections, Account links, Statements and Raw records, with the tools
  their routes make -- on a port fixed at 8000, that it reads external
  accounts, what it does not carry, its storage, and its documentation.
  `__main__` connects with the declaration alone; so does the e2e's desk.
  The parts the pages import moved to `declared.py`.
- **Its documentation** (`docs/`): overview, setup, pages, troubleshooting
  and terms, written from this README and the code, linking the custody
  role's page.
- **A controlled clock in the e2e** (decision 66): the synthetic OANDA takes
  `{"clock": "<RFC 3339 time>"}` through `ask()`, its own times running on
  from that moment, kept with its state, never set back, the day set taken
  as rolled. `e2e/orders.py` sets it to the coming Tuesday at 12:00 New York
  before the order path, so the trade dates it reports never depend on the
  day the e2e runs (it failed from Thursday 17:00 to Saturday 17:00 New
  York).
- **The suites**: custody's v24 case, a holding the venue names by its code
  alone, the metal XAU the platform does not hold, recorded unresolved;
  connectivity's give-up case not presented (no trade is given up at
  OANDA); the two cases every suite holds answered by the SDK.
- **The pins**: open-meridian 0.29.0 and plugin-python:0.29.0, core v25's
  runtime and harness, and `meridian` 0.1.46 in `check.yaml`, each as released
  (core abf028f, chart 0.1.306, by tag and digest).
- **`make e2e`** takes the harness down with `--profile runner`.

## 0.3.0

On SDK 0.27.0 and contract v23. An admin picks the OANDA account from those
the token reaches, instead of typing its ID (approved by the product owner,
2026-10-14).

- `account_id` is optional. Once the token is set, the plugin lists the v20
  accounts it reaches (OANDA's `GET /v3/accounts`) on Connections, under
  Account, on every poll and settings change, and an admin picks one with
  "Use this account": the `choose_account` tool, checked by
  `check_choose_account`, at admin on both `custody` and `connectivity`, as
  the setting serving both asks. It is a declared act, like
  `enable_trading`: the dashboard admits it as the person's, records an
  agent's call as theirs, and can hold it for a second person. The plugin
  sets no setting: who chose which account, and when, is kept in its
  storage (`state/account.json`), per environment.
- Where the token reaches exactly one usable account, it is preselected,
  but nothing is read or traded until an admin confirms it. Nothing is
  picked silently.
- A v1 account, whose ID is digits only, is listed as unusable, with the
  reason. It cannot be chosen.
- An `account_id` setting that is set is the account, as before. While it
  is set, Connections lists the accounts and offers no choice.
- The connection is still the environment and the account: choosing a
  different account makes a new connection, read-only until trading is
  enabled on it.
- Switching away turns trading off (approved by the product owner,
  2026-10-14). When the connection in use changes, by `choose_account` or
  by the `account_id` or `environment` setting, trading on the one left is
  turned off. The change is recorded in the plugin's storage
  (`state/trading-off.json`): which connection, the one now in use, the
  change that caused it and who made it, when, and who had enabled it.
  Returning to it finds it read-only, and a person enables trading again.
  The connection last in use is kept (`state/in-use.json`), so a settings
  change made while the plugin was stopped is caught when it starts.
  `read_connections` answers `trading_turned_off` for the connection in
  use, and Connections marks it.
- `read_connections` answers the accounts the token reaches (`reached`),
  the one in use and where it came from, and the one preselected.
- Until an account is chosen, the plugin reports itself waiting for it.
- The synthetic OANDA's token also reaches a v1 account. The e2e chooses
  the synthetic account on Connections before linking it.

## 0.2.0

On SDK 0.27.0 and contract v23. The 0.1.0 suite gaps are closed.

- `connectivity`, the value-date roll (W11.25): at each 17:00 New York
  roll, each open trade's financing is a charge of kind financing
  (`FEE_KIND_FINANCING`) on the placement that opened it. Then a
  `ReportValueDateRoll` per pair moves the pending lines from their value
  date to the date read's answer for the roll's moment, its `charge_ids`
  the pair's charges. A roll waits, never guessed, while the date read
  gives no answer. A fill reported after OANDA rolled it gets a catch-up
  roll, and so does a venue-originated fill accepted after a roll.
- A fill's commission is a fee of kind commission (`FEE_KIND_COMMISSION`).
- Market orders: the order's `quote_currency` names the pair, so a market
  order is sent rather than rejected `unsupported`.
- Venue-originated fills: a fill of an order taken in OANDA's own platform
  or app, or made by OANDA, in a currency pair, is reported on no placement
  for the `ems` to accept or answer DK. Its trade's financing is kept
  unplaced, then charged again on the placement an accept creates. The
  catalogue says `EXECUTION_ACKNOWLEDGEMENT_NONE`.
- Suites: connectivity's settlement-date case passes on the spot inputs.
  Busts, and a later fill on an accepted placement, are not presented (OANDA
  has neither). Custody's sweep-fund, split, plan-code and
  currency-not-stated cases are not presented, with why. The suite tests
  assert that the report passes, with no skip or expected failure.
- The synthetic OANDA rolls at the 17:00 cut, and takes orders "in OANDA's
  app" and rolls on request (`ask()`), for the e2e.
- meridian 0.1.43 in check.yaml.

## 0.1.0

The first version, on SDK 0.26.0 and contract v22 (meridian-design
tasks/sdk-contract/an-fx-ccm-plugin-trades-spot; ruled 2026-10-13).

- `connectivity`: placements sent to OANDA's v20 order endpoint as signed
  units of the base currency. Facts come from the account's transactions:
  acknowledgements, rejections (`unsupported`, or venue rejected with OANDA's
  reject reason), fills with the date read's spot value date (a fill waits,
  never guessed, while the date read gives no answer), financing charges on
  the placement that opened the trade, cancels, replaces, pause and stop.
  An unanswered send is resolved by asking, never by sending again. The
  catalogue is published, and none is offered while the connection is
  read-only.
- `custody`: the account, its sync status, its statement (rolling spot as
  two cash legs) and its activity back to the account's opening.
- Settings: `environment` (`practice` | `live`), `token` (secret) and
  `account_id`.
- Raw records: `activity`, `responses` and `trading`, archivable.
- The synthetic OANDA serves sample data, the tests and `make e2e`.