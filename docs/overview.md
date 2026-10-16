# Overview

The OANDA plugin connects an Open Meridian deployment to an OANDA spot FX
account through OANDA's v20 API: it places the desk's FX orders at OANDA,
reports what OANDA did with each, and brings in the account as the
custodian's view.

It is a custody plugin and a connectivity plugin: the first-use path, the
common pages and the Summary figures of each are described once on
[Using a custody plugin](https://open-meridian.dev/how-to/using-a-custody-plugin/).
These sections cover what is OANDA's own.

## Its two roles

**Connectivity**: it places the orders your execution plugin (an `ems`)
sends it at OANDA, and reports acknowledgements, rejections, fills,
commissions, each trade's financing, and each daily value-date roll. It also
reports the fills of orders taken in OANDA's own platform or app, or by
OANDA itself (a stop loss, a margin closeout), on no order, for your
execution plugin to accept to an order or answer DK.

**Custody**: it reads the chosen account as the custodian's view: the
account, its sync status, a statement each read, and its activity.

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

- It records no market data: OANDA's quotes in the lake are a later
  version's.
- It never turns a pair round: an order in US dollars priced in euros is
  rejected, naming EUR_USD.
- It never sends an order twice: an unanswered send is resolved by asking
  OANDA for the order by its client ID.
- It speaks only OANDA's v20 API: a v1 account is listed as unusable.
- OANDA is a trademark of its owner. This project is not affiliated with or
  endorsed by OANDA.
