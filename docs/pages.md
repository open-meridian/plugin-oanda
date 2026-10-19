# Pages

You open the plugin from the dashboard's home by the button for the level
you hold on it: **Manage** (admin), **Open** (write) or **View** (read).

## Connection and Datasets

The data role's, the same on every data plugin, under Manage for an admin
of the data role. Neither shows a price: prices are read by the plugins
entitled to them. Each is also a tool an agent can call (`read_connection`,
`read_datasets`).

- **Connection**: the mode (Practice, Live or Sample data); whether the
  access token is set, never its value; the environment and its badge,
  **Demo** only while the setting says Practice and the session reached
  OANDA's practice host; OANDA's limits and the calls made; OANDA as the
  deployment's venue master holds it; and the pricing stream: its state,
  the pairs on it, its reconnects and messages.
- **Datasets**: `live` and `daily` as declared, licensed or not, what each
  recorded and declined since the plugin started; each want, the ones kept
  current first; and OANDA's pairs, how many serve, and any currency your
  deployment does not hold.

## Connections

Under Manage, for an admin of the custody or connectivity role. The
connection in use: its environment and its **Demo** or **Live** badge, the
account and who chose it, whether OANDA answers, and whether trading is
enabled, with **Enable trading** for an admin of the
connectivity role. Under Account, the v20 accounts the token reaches, each
usable or not with why, and **Use this account**. A notice says when a
change of account or environment turned trading off.

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

Under Open and View. What OANDA answered, as received, one read at a time,
and the pricing answer behind a price or a quote in the lake, followed from
its reference, with what moved past its window to the deployment's archive;
under Open,
**Restore** brings an archived one back for seven days.

## Summary and Settings

Drawn by the dashboard: the plugin's health, the custody role's figures and
the data role's (its stream, and when it last recorded), and the settings
form. See [Setup](setup.md).
