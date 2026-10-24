# Overview

The OANDA plugin connects an Open Meridian deployment to an OANDA spot FX
account through OANDA's v20 API: it places the desk's FX orders at OANDA,
reports what OANDA did with each, brings in the account as the custodian's
view, and records OANDA's prices in your deployment's lake.

It holds three roles, `data`, `custody` and `connectivity`: the first-use
path, the common pages and the Summary figures of the first two are
described once, on
[Using a data plugin](https://open-meridian.dev/how-to/using-a-data-plugin/)
and
[Using a custody plugin](https://open-meridian.dev/how-to/using-a-custody-plugin/).
These sections cover what is OANDA's own.

## Its data role

**Data**: it records OANDA's prices in the lake, for the plugins your
deployment entitles to read them. Two datasets:

- **live**: each currency pair's best bid and ask, with the liquidity OANDA
  states beside each, as a quote, and their mid as a price. A pair kept
  current is recorded from OANDA's pricing stream, at most once a second.
  A price OANDA says is not tradeable (outside FX hours) is marked halted.
  Its default terms keep the rows for 90 days.
- **daily**: each pair's close, the mid of OANDA's daily candle, on OANDA's
  day ending at 17:00 New York. The day still forming is restated as it
  fills.

A pair is a price of its base currency in its quote currency, exactly as
OANDA quotes it: EUR_USD is the euro's price in dollars. Nothing is inverted
or crossed. Only currency pairs are recorded, not metals or CFDs.

OANDA's prices are its own and real time: neither dataset declares a delay.
The pricing stream sends a price only when it changes, and a heartbeat every
five seconds. Each heartbeat is recorded as the live dataset's sign of life,
so an unchanged quote is still known to be current.

## Its trading and custody roles

**Connectivity**: it places the orders your execution plugin (an `ems`)
sends it at OANDA, and reports acknowledgements, rejections, fills,
commissions, each trade's financing, and each daily value-date roll. It also
reports the fills of orders taken in OANDA's own platform or app, or by
OANDA itself (a stop loss, a margin closeout), on no order, for your
execution plugin to accept to an order or answer DK. It takes market,
limit and stop orders, each placed directly at OANDA: OANDA is the
counterparty to every order, so there is no request for quote.

**Custody**: it reads the chosen account as the custodian's view: the
account, its sync status, a statement each read, and its activity.

All three reach the same account with the same token.

## Practice and live

The **Environment** setting is Practice or Live: which of OANDA's hosts the
account is on. The plugin also reports which host its session actually
reached, on its catalogue: OANDA's practice host or its live one. A
connection is **Demo** only while both say practice. Your deployment lets
agents place orders only on a Demo connection; anything else, including a
mismatch, counts as Live.

## How a position is shown

Each open trade is two cash legs: its units of the base currency, and the
units times its price, negated, of the quote currency. Each leg is pending
to the value date the position now rolls to. The account's currency carries
the balance, settled.

Activity is each transaction that moved cash: fills are purchases or sales,
commissions are fees, each roll's financing is interest, a profit converted
to the account's currency is a journal, and funds moved in or out are
contributions, withdrawals or transfers.

## The daily roll

At each 17:00 New York, OANDA rolls every open trade to the next spot date.
The plugin reports each trade's financing as a charge on the placement whose
fill opened it, then a value-date roll for each pair, moving the pending
lines to the date the deployment's date read gives for the roll's moment. A
fill's value date is the pair's spot date from that same date read; OANDA's
fill states none, and a fill is never sent with a guessed date.

## What it does not do

- It cannot read back a gap in its quotes: OANDA keeps no history of its
  quotes. After a restart the stream starts again from each pair's price
  now.
- It never turns a pair round: an order in US dollars priced in euros is
  rejected, naming EUR_USD.
- It never sends an order twice: an unanswered send is resolved by asking
  OANDA for the order by its client ID.
- It speaks only OANDA's v20 API: a v1 account is listed as unusable.
- OANDA is a trademark of its owner. This project is not affiliated with or
  endorsed by OANDA.
