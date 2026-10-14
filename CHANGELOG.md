# Changelog

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
