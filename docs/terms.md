# Terms

## OANDA's terms

OANDA is a third party. A person trades and holds an account with OANDA
under their own agreement with OANDA, and the use of OANDA's API is settled
by the deployment admin: its terms are at
[developer.oanda.com](https://developer.oanda.com). Read them before
entering a token. OANDA Corporation's API License Agreement (August 2026)
puts its rates to internal use only -- research, analysis and data
processing by the account holder, or within its organisation -- and forbids
redistributing, displaying or otherwise providing them to any third party,
clients and customers included. Each of the plugin's datasets describes
these terms in words on Data sources, and declares no licence. Which broker a person may trade with is between that
person and the broker.

This project is not affiliated with or endorsed by OANDA. Wherever the
plugin is listed, the listing is not an endorsement of it or of OANDA.

## Your due diligence

Your firm carries its own due diligence, on OANDA and on this plugin: the
terms, security, and whether each is fit for your firm's purpose. Start on a
practice account.

## Your data

- What the plugin reads and records stays in your deployment.
- The token reaches the plugin as a secret setting and nowhere else: it is
  never logged, shown or kept in what the plugin stores.
- OANDA's answers are kept, the token removed, in storage for as long as
  each kind's window says, then archived, kept or deleted as an admin
  chooses on Settings.
- OANDA's prices recorded in the lake are kept as the datasets' licences
  say, which the deployment admin sets on Data sources; until one is set,
  the lake keeps the rows and reads every use right restrictively.

## Licence

Apache License 2.0. The plugin is provided "as is", without warranties or
conditions of any kind; see its LICENSE file.
