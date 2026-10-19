# Troubleshooting

## Settings missing

The status dot names the missing settings, and nothing is read. Enter them
on Manage → Settings ([Setup](setup.md)).

## OANDA refused the token

The token was revoked, or is for the other environment. Make a new token in
OANDA's hub for the account's login and enter it, with the matching
Environment.

## No account to choose

The token reaches no v20 account, or only v1 accounts, which are listed as
unusable with why. This plugin speaks only OANDA's v20 API.

## Every order is rejected as unsupported

Trading is not enabled on the connection in use. A new connection, or one
returned to after a change of account or environment, is read-only until an
admin of the connectivity role enables it on Connections.

## An order rejected naming a pair

The order is in one currency priced in another, the other way round from
how OANDA quotes the pair. Place it in the pair's base currency, priced in
its quote currency.

## A fill waits

A fill is sent only with its value date, from the deployment's date read.
Where the date read gives no answer, the fill waits and is asked about again
on each read.

## A fill on no order

OANDA reported a fill of an order taken in its own app, or by OANDA itself.
Your execution plugin's desk accepts it to an order or answers it DK.

## Stale

OANDA has not answered for longer than Stale after. It is read again on the
next poll; Connections says what OANDA last answered.

## A connection shows Live, or "environment not confirmed"

The badge is Demo only while the Environment setting says Practice and the
session reached OANDA's practice host. "Live (environment not confirmed)"
means the two disagree: check the Environment matches the token's account,
or save the Environment again if Connections says it was saved as a value
this version no longer offers (`practice`, from before 0.5.0). Until then
agents may not place orders on it.

## A want declined

On Datasets, a subject is declined "not covered" where no currency pair
OANDA quotes has it as the base: OANDA quotes USD_JPY, so the yen's own
record is not covered, and nothing is inverted. "Beyond history" answers a
past range of the live dataset, since OANDA keeps no history of its quotes.
"Source silent" means OANDA said nothing, or no account is chosen yet.

## The stream is waiting

The pricing stream dropped and opens again, waiting longer each time, a
minute at most. Connection shows its last error.
