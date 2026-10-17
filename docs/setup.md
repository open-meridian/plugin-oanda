# Setup

Each step by an admin of the plugin, on its roles as each says. The plugin
must already be launched, its roles approved by a deployment admin.

## Before launching

- An OANDA account, and a personal access token made in OANDA's hub for the
  account's login. OANDA does not keep the token either: if it is lost,
  revoke it there and make a new one.
- A practice account first. Which broker a person may trade with is between
  that person and the broker, and OANDA's API terms are settled by the
  deployment admin: see [Terms](terms.md).

## 1. Enter the token

**Manage → Settings.**

1. **Environment**, first: Practice or Live, the OANDA host the account is
   on. Practice by default.
2. **Access token**: the token from OANDA's hub. A secret setting: it shows
   only "set" or "not set", and is never shown, logged or kept.
3. **Account ID**: leave it empty, to choose the account on Connections.
4. **Read every** and **Stale after**: how often the account is read (five
   minutes by default), and how old the last good read may be before the
   account is reported stale (24 hours).

## 2. Choose the account

**Manage → Connections**, under Account: the v20 accounts the token reaches.
Choose one and press **Use this account**. Where the token reaches exactly
one usable account it is preselected, but nothing is read or traded until an
admin confirms it. Choosing needs admin on both of the plugin's roles. Who
chose which account, and when, is kept in the plugin's storage.

An Account ID entered in Settings is the account while it is set, and
Connections offers no choice; clear it to choose on Connections.

## 3. Link the account

**Manage → Account links**: link the OANDA account to one of the
deployment's accounts. Nothing is recorded for it until it is linked.

## 4. Trading, only if you place orders through it

A new connection is read-only: every order on it is rejected as
unsupported. An admin of the connectivity role presses **Enable trading** on
Connections. The connection is the environment together with the account,
so changing either makes a new connection, read-only again; switching away
also turns trading off on the old one, and returning finds it read-only.
Each such change is recorded with who made it and when.

Then your execution plugin's admin maps the account the desk trades to the
code this plugin names it by.

## Sample data

On a development deployment, **Sample data** trades with and reads a
simulated OANDA built into the plugin, so it can be tried before any token
exists. Nothing reaches OANDA.
