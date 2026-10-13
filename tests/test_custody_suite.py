"""The custody suite: every case OANDA presents, from this plugin's own
Syncer (custody.py) over the synthetic OANDA (synthetic.py), only the
sidecar stood in for. SIMULATED ONLY: nothing reaches OANDA.

Each case is mapped to what OANDA's v20 account states for it, in OANDA's
own terms (`PRODUCERS`), or marked not presented with why where the suite
lets a case be: a closed-list value OANDA never presents, or a case `about`
a kind of work this plugin never does (`NOT_PRESENTED`).

Seven required cases describe a securities custodian OANDA is not, and are
held here as the suite's gap, each with why (`GAPS`), reported for the
product owner rather than gamed: a money market fund counted in cash (two
cases), a currency not stated, a split moving no cash, and a plan's own
fund code (three cases). OANDA's v20 account holds currency positions only,
always in a stated currency, and has no fund, split or plan code.

How the mapped ones are presented, where it is not plain:

- `a-security-holding` ("one security, its quantity, settled quantity and
  currency stated"): an OANDA account with no open position holds one
  asset, its cash in the account's currency, whose balance OANDA states;
  OANDA holds no security.
- `settled-quantity-not-stated` and `pending-by-value-date`: an open
  EUR_USD position's EUR leg, whose units OANDA states and whose settled
  quantity it does not: 0, by the rolling rule, its units pending to the
  value date the date read gives (computed, not stated by OANDA).
- `kind-not-converted-on-an-ambiguous-resolve`: an open XAU_USD position,
  which OANDA types METAL, its XAU leg's ISO 4217 code meeting two records.
- `account-kind-not-known`: an account whose margin rate is 1 (no
  leverage); OANDA states no account type, only its margin rate.
- `sync-needs-sign-in`: OANDA refusing the token (HTTP 401): a person makes
  a new one in OANDA's hub. `sync-stale`: OANDA not answering, the last good
  read older than Stale after.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

from meridian.suites import Recorder, names, run, suite

from oanda.linking import Links
from oanda.venue import VenueError

from rigs import ACCOUNT, CustodyRig, Producer

NOT_PRESENTED = {
    "sync-disabled": (
        "OANDA states no disabled connection: its token is answered (current), refused "
        "(needs sign-in) or not answered (stale)"
    ),
    "sync-delayed-by-design": "OANDA's v20 API answers the account as it stands, never late",
    "sync-holdings-unavailable": "OANDA's v20 API states every account's positions",
    "account-kind-cash": (
        "OANDA states no account type, only a margin rate: below 1 margin, any other not "
        "known, as reported; never a cash account"
    ),
    "account-kind-retirement": "OANDA's v20 accounts are trading accounts, never retirement",
    "activity-reinvestment": "OANDA's account holds no fund to reinvest in",
    "activity-dividend": "OANDA's US account holds no security that pays a dividend",
    "activity-tax": "OANDA's v20 transactions withhold no tax",
    "activity-split": "OANDA's account holds no security to split",
    "activity-corporate-action": "OANDA's account holds no security a corporate action meets",
    "reads-a-calendar": (
        "the legs' value dates come from the date read (ComputeDate, the pair's spot-date "
        "rule); the plugin reads no calendar's days"
    ),
    "asks-for-a-settlement-date": (
        "the legs' value dates are the pair's spot date, asked with the date read's spot "
        "inputs, never an instrument's settlement-date rule"
    ),
}

#: Required cases OANDA's account cannot present, and why: the suite's gap.
GAPS = {
    "cash-net-of-a-fund-counted-in-cash": "OANDA's account holds no money market fund",
    "fund-larger-than-cash": "OANDA's account holds no money market fund",
    "currency-not-stated": "OANDA states the currency of every position and amount",
    "activity-moves-no-cash": "OANDA's account holds no security to split",
    "activity-plan-code-not-linked": "OANDA has no plan's own fund code",
    "activity-plan-code-linked-by-a-person": "OANDA has no plan's own fund code",
    "activity-re-resolved-when-a-plan-code-is-linked-later": (
        "OANDA has no plan's own fund code"
    ),
}


async def read(recorder: Recorder) -> None:
    rig = CustodyRig(recorder)
    await rig.syncer.run_once()


async def cash_only(recorder: Recorder) -> None:
    """The account with no open position: one asset, its cash."""
    rig = CustodyRig(recorder)
    for trade in rig.venue._state["trades"].values():
        trade["state"] = "CLOSED"
    await rig.syncer.run_once()


async def no_leverage(recorder: Recorder) -> None:
    rig = CustodyRig(recorder)
    rig.venue._state["account"]["marginRate"] = "1"
    await rig.syncer.run_once()


def failing(status: int | None, after: timedelta = timedelta()) -> Producer:
    async def produce(recorder: Recorder) -> None:
        rig = CustodyRig(recorder)
        await rig.syncer.run_once()
        rig.clock.at += after

        async def refused(account_id: str) -> dict[str, object]:
            raise VenueError("reading the account", status)

        rig.venue.account = refused  # type: ignore[method-assign]
        await rig.syncer.run_once()

    return produce


async def linking_page(recorder: Recorder) -> None:
    """The Account links tab reads the accounts to link, as the page does."""
    await Links(cast(Any, recorder)).offered("")


async def a_new_account(recorder: Recorder) -> None:
    """The account map's create form, as the page sends it: OANDA's margin
    account to a new account, held at OANDA."""
    links = Links(cast(Any, recorder), settle_seconds=0)
    await links.link("", ACCOUNT, "", "OANDA practice", "OANDA", "margin")


PRODUCERS: dict[str, Producer] = {
    "sync-current": read,
    "sync-stale": failing(None, timedelta(hours=25)),
    "sync-needs-sign-in": failing(401),
    "account-kind-margin": read,
    "account-kind-not-known": no_leverage,
    "read-accounts-for-linking": linking_page,
    "link-a-new-account": a_new_account,
    "a-security-holding": cash_only,
    "institution-not-stated": read,
    "settled-quantity-not-stated": read,
    "pending-by-value-date": read,
    "resolve-states-what-the-source-states": read,
    "kind-not-converted-on-an-ambiguous-resolve": read,
    "activity-purchase": read,
    "activity-sale": read,
    "activity-interest": read,
    "activity-fee": read,
    "activity-transfer-in": read,
    "activity-transfer-out": read,
    "activity-contribution": read,
    "activity-withdrawal": read,
    "activity-journal": read,
    "activity-kind-not-converted": read,
    "activity-signs": read,
    "history-from-stated": read,
}


def test_every_case_is_mapped_said_not_presented_or_held_as_a_gap() -> None:
    every = set(names("custody"))
    assert every == set(PRODUCERS) | set(NOT_PRESENTED) | set(GAPS)
    assert not set(PRODUCERS) & set(NOT_PRESENTED)
    assert not set(PRODUCERS) & set(GAPS)
    cases = {case.name: case for case in suite("custody").cases}
    assert all(cases[name].closed_list or cases[name].about for name in NOT_PRESENTED), (
        "a required case is never skipped"
    )
    assert not any(cases[name].closed_list or cases[name].about for name in GAPS)


def test_the_custody_suite_passes_but_for_its_gaps() -> None:
    report = run("custody", PRODUCERS, not_presented=NOT_PRESENTED, instance_id="oanda")
    assert set(report.failures) == set(GAPS), report.failures
    assert all("no producer" in why for why in report.failures.values())
    assert len(report.passed_cases) == len(PRODUCERS)
    assert sorted(report.not_presented) == sorted(NOT_PRESENTED)


def test_the_legs_net_to_the_balance_and_the_open_profit() -> None:
    """The statement's rows: USD settled at the balance with the EUR_USD and
    XAU_USD quote legs pending; EUR and XAU pending, settled 0."""
    import asyncio

    recorder = Recorder("oanda")
    asyncio.run(read(recorder))
    rows = {
        (h.instrument_id or h.unresolved_identifiers[0].value): h
        for h in recorder.on("RecordHolding")
    }
    from meridian.operations import as_decimal

    usd = rows["INS-01JUSD00000000000000000001"]
    # USD carries the XAU_USD trade's quote leg, whose value date no rule
    # gives: its split is not stated.
    assert not usd.HasField("settle_date_quantity") and list(usd.pending) == []
    assert as_decimal(usd.quantity) == Decimal("85493.88600")
    eur = rows["INS-01JEUR00000000000000000001"]
    assert as_decimal(eur.settle_date_quantity) == 0
    assert [(p.value_date, as_decimal(p.quantity)) for p in eur.pending] == [
        ("2026-10-15", Decimal(10000))
    ]
    xau = rows["XAU"]
    assert not xau.HasField("settle_date_quantity"), (
        "a metal's value date is worked out by no rule"
    )
