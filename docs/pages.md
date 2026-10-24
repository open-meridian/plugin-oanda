# Pages

You open the plugin from the dashboard's home by the button for the level
you hold on it: **Manage** (admin), **Open** (write) or **View** (read).

## Connection

One page for the data and connectivity roles, the same on every plugin
holding both, under Manage for an admin of either. It shows no price and no
account's data, and is also a tool an agent can call (`read_connection`).
Beside each other where the screen is wide enough:

- **Key, Limits and use, Stream**: the mode (Practice, Live, Sample data or
  No key); whether the access token is set, never its value; OANDA's limits
  and the calls made; OANDA as the deployment's venue master holds it; and
  the pricing stream: its state, the pairs on it, its reconnects and
  messages.
- **Sessions**: each session's latency, once measured: the pricing
  stream's and the order calls'.
- **Environment, Accounts, Placements**: the environment and its badge,
  **Demo** only while the setting says Practice and the session reached
  OANDA's practice host; the account in use, read-only or with trading
  enabled, with **Enable trading** for an admin of the connectivity role (a
  new connection is read-only until then); and the placements working, held
  back or asked about. A notice says when a change of account turned
  trading off, and how many fills wait for a value date.

## Datasets

Under Manage, for an admin of the data role (`read_datasets`): `live` and
`daily` as declared, licensed or not, what each recorded and declined since
the plugin started; each want, the ones kept current first; and OANDA's
pairs, how many serve, and any currency your deployment does not hold.

## Connections

Under Manage, for an admin of the custody or connectivity role. The
connection in use: its environment and its **Demo** or **Live** badge, the
account and who chose it, and whether OANDA answers. Under Account, the v20
accounts the token reaches, each usable or not with why, and **Use this
account**. Its trading is on Connection.

## Account links

Under Manage, for an admin of the custody role. The OANDA account the
plugin reads, beside the deployment's accounts, and its link.

## Statements

Under Open and View (from 0.5.0 at `/statements`; the plugin's first path
is the data role's Connection). The linked account you may read: its
statement as of
its last read, each currency's leg, settled or pending to its value date,
and its sync state. Under Open, the head reads OANDA now.

## Raw records

Under Open and View. Each kind of record kept, its window and what becomes
of it past the window. A row's reference opens the record behind it, as
OANDA sent it: a read, a trading exchange, or the pricing answer behind a
price or a quote in the lake. It is shown to a person who may read the
account it is linked or mapped to. Under Open, **Restore** brings an
archived one back for seven days.

## Summary and Settings

Drawn by the dashboard: the plugin's health, the custody role's figures and
the data role's (its stream, and when it last recorded), and the settings
form. See [Setup](setup.md).
