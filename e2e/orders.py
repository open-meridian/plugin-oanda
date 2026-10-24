"""`make e2e`'s order path (contract v23), after the custody steps, in the
same harness: OANDA holding `custody` and `connectivity` in sample data, and
the desk (e2e/desk.py) holding `portfolio`, `oms` and `ems`. SIMULATED
ONLY: the synthetic OANDA (src/oanda/synthetic.py); no OANDA host, practice
or live, is reached.

1. Set up: the synthetic OANDA's clock set to the coming Tuesday at 12:00
   New York (decision 66), so its fills trade on a Tuesday and its roll is
   that day's 17:00, whatever day the e2e runs; an account for the orders,
   the desk's grants on it, EUR's and USD's cash instruments named; OANDA made available and linked to the
   desk, the account mapped to the synthetic account's code; trading
   enabled on the connection under the connectivity role's admin (a new
   connection is read-only); releases not waiting for compliance.
2. A buy of 10,000 EUR, limit 1.08000 USD, naming USD as its quote
   currency: placed at OANDA as EUR_USD, acknowledged with OANDA's order ID,
   filled there, and recorded with the value date the date read gives (the
   harness's EUR/USD spot-date rule), EUR pending to it.
3. A market buy of 5,000 EUR, which names its pair by its quote currency
   (contract v23): placed at OANDA as a market order and filled at once.
4. An order taken in OANDA's own app, 2,000 EUR: its fill reported on no
   placement, held unmatched in the book for the desk.
5. OANDA's 17:00 New York roll: each trade's financing charged, and the
   value-date roll moving EUR's pending 15,000 from the fills' value date to
   the date read's answer for the roll's moment.
6. The desk accepts the app's fill to a new order: the plugin charges its
   financing again on the placement the accept made and reports a catch-up
   roll, so all 17,000 pend to the rolled date.
7. A replace and a cancel the desk asks for: a working placement of
   1,000,000 replaced at OANDA to a limit of 1.07500, OANDA's new order ID
   followed and the request applied; a replace to at the open, which OANDA
   has no time in force for, refused `unsupported`, the placement working
   on; then cancelled at OANDA, then in the book.
8. A pause: a placement held back, queued; lifted, it is sent. A stop: OANDA
   cancels it at once, the cancel reported naming the stop; lifted again.
9. OANDA restarted with a placement working: it reads the book again, sends
   no second order, and cancels it at OANDA when the desk asks.
10. `store book` prints the orders, placements and executions.

Standard library only; the Makefile hands it the harness's compose command
as its arguments.
"""

import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from decimal import Decimal
from zoneinfo import ZoneInfo

COMPOSE = sys.argv[1:]
LOG = ".e2e/orders.log"
CODE = "101-001-0000000-001"
WORKS = "1000000"
# EUR's cash instrument as `store book` prints it: the platform's record by
# its INS- ID (contract v24's one-ID model), the harness's published format.
EUR_IN_BOOK = "INS-01JEUR00000000000000000001"


class Failed(Exception):
    pass


def log(text):
    with open(LOG, "a", encoding="utf-8") as kept:
        kept.write(text + "\n")


def compose(*args, check=True):
    done = subprocess.run(COMPOSE + list(args), capture_output=True, text=True, timeout=600)
    log(f"$ compose {' '.join(args)[:300]}\n{done.stdout}{done.stderr}")
    if check and done.returncode != 0:
        raise Failed(f"compose {' '.join(args)[:200]}: {(done.stdout + done.stderr).strip()[-600:]}")
    return done


def runner(*args):
    return compose("run", "--rm", "-T", "runner", *args).stdout


def must(condition, why):
    if not condition:
        raise Failed(why)


def until(seconds, attempt, what):
    deadline = time.monotonic() + seconds
    last = None
    while True:
        try:
            answer = attempt()
        except Failed as failed:
            answer, last = None, failed
        if answer:
            return answer
        if time.monotonic() > deadline:
            raise Failed(f"{what}, in {seconds} seconds" + (f": {last}" if last else ""))
        time.sleep(2)


def desk(level, path):
    said = runner("page", "--instance", "desk", "--level", level, path)
    status, _, body = said.partition("\n")
    must(status.strip() == "200", f"desk {path} answered {said[-600:]}")
    return json.loads(body)


def order_of(account, order_id):
    for order in desk("write", f"/orders?account={account}")["orders"]:
        if order["order_id"] == order_id:
            return order
    raise Failed(f"{order_id} is not listed")


def placement_of(order, placement_id):
    for held in order["placements"]:
        if held["placement_id"] == placement_id:
            return held
    raise Failed(f"{placement_id} is not on {order['order_id']}")


def ask_oanda(request):
    """Ask the synthetic OANDA for an order in its own app, or a roll: a
    request file in its state directory, taken on its next step."""
    compose(
        "exec", "-T", "oanda", "python", "-c",
        "from meridian import edge; from oanda.synthetic import ask;"
        f"ask(edge.storage_dir(), {request!r})",
    )


def book():
    return compose("run", "--rm", "-T", "store", "book").stdout.splitlines()


def pending(lines, instrument=EUR_IN_BOOK):
    """The account's pending long lines in an instrument, as the book prints
    it, by value date."""
    held = {}
    for line in lines:
        parts = line.split("|")
        if parts[:4] == ["pending", "E2E Orders", instrument, "long"]:
            held[parts[4]] = Decimal(parts[5])
    return {day: quantity for day, quantity in held.items() if quantity}


def entries(lines, kind):
    return [line for line in lines if line.startswith(f"entry|E2E Orders|{kind}|")]


def placed(account, instrument, quantity, key, extra=""):
    """An order proposed, released, claimed and placed at OANDA."""
    proposed = desk(
        "write",
        f"/propose?account={account}&instrument={instrument}&quantity={quantity}&key={key}{extra}",
    )
    must(proposed.get("order_id"), f"{key}: proposed: {proposed}")
    order = proposed["order_id"]
    released = desk("write", f"/release?order={order}")
    must(released.get("state") == "ORDER_STATE_RELEASED", f"{key}: released: {released}")
    desk("write", f"/claim?order={order}")
    placement = desk("write", f"/place?order={order}&quantity={quantity}&code={CODE}")
    must(placement.get("placement_id"), f"{key}: placed: {placement}")
    return order, placement["placement_id"]


def placement_state(account, order, placement_id, *states):
    def attempt():
        held = placement_of(order_of(account, order), placement_id)
        return held if held["state"] in states else None

    return attempt


def oanda_orders():
    """The synthetic OANDA's orders, as its state in the plugin's storage
    holds them: each placement's client ID, and its state."""
    said = compose(
        "exec", "-T", "oanda", "python", "-c",
        "import json; from meridian import edge;"
        "p = edge.storage_dir() / 'synthetic-oanda' / 'state.json';"
        "held = json.loads(p.read_text()) if p.exists() else {};"
        "print(json.dumps([[(o.get('clientExtensions') or {}).get('id', ''), o.get('state'),"
        " bool(o.get('replacesOrderID'))] for o in held.get('orders', {}).values()]))",
    ).stdout
    return json.loads(said.strip().splitlines()[-1])


def controlled_clock(now=None):
    """The moment the synthetic OANDA's clock is set to (decision 66): the
    coming Tuesday at 12:00 New York, strictly after now, so a clock is only
    set forward. Its fills trade that Tuesday, spot Thursday; its roll at
    17:00 moves them to Wednesday's spot date, Friday, the same every run."""
    new_york = ZoneInfo("America/New_York")
    now = now or datetime.now(new_york)
    day = now.date() + timedelta(days=(1 - now.weekday()) % 7)
    at = datetime.combine(day, clock_time(12), new_york)
    if at <= now:
        at += timedelta(days=7)
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def oanda_clock_ahead():
    """How far the synthetic OANDA's clock runs ahead of the wall clock, in
    nanoseconds, as its state holds it: 0 until a clock is set."""
    said = compose(
        "exec", "-T", "oanda", "python", "-c",
        "import json; from meridian import edge;"
        "p = edge.storage_dir() / 'synthetic-oanda' / 'state.json';"
        "held = json.loads(p.read_text()) if p.exists() else {};"
        "print(int(held.get('clock_ahead_ns', 0)))",
    ).stdout
    return int(said.strip().splitlines()[-1])


def sent_once(placement_id):
    return [o for o in oanda_orders() if o[0] == placement_id and not o[2]]


def run():
    # 1. Set up, once the desk has registered: on a fast runner the custody
    # steps can end before it has.
    runner("ready", "--instance", "desk")
    clock = controlled_clock()
    ask_oanda({"clock": clock})
    until(60, lambda: oanda_clock_ahead() > 0, f"step 1, the synthetic OANDA's clock not set to {clock}")
    account = runner("account", "E2E Orders", "--instance", "desk").strip().splitlines()[-1]
    for role in ("portfolio", "oms", "ems"):
        runner("grant", "--instance", "desk", "--level", "write", "--role", role, "--accounts", account)
    runner("grant", "--instance", "desk", "--level", "admin", "--role", "ems")
    # OANDA's connectivity admin was granted before the account was chosen
    # (custody.py, step 2).
    eur = desk("write", "/resolve?iso4217=EUR")["instrument_id"]
    usd = desk("write", "/resolve?iso4217=USD")["instrument_id"]
    must(eur and usd, f"step 1, EUR's and USD's cash instruments: {eur}, {usd}")
    runner("connectivity", "available", "--instance", "oanda")
    runner("connectivity", "link", "--instance", "oanda", "--ems", "desk",
           "--user", "person:local|harness")
    mapped = desk("admin", f"/map?account={account}&code={CODE}")
    must(mapped == {"mapped": CODE}, f"step 1, mapped: {mapped}")
    # From 0.6.0 on Connection, the SDK's one page for the data role and
    # connectivity (457), by the account's venue account code.
    runner("form", "--instance", "oanda", "--level", "admin", "--page", "/",
           "--post", "/enable-trading", f"venue_account_code={CODE}",
           "--expect", "Trading enabled on")
    runner("attribute", account, "waits=false", "note=No compliance plugin in this run.")

    quoted = f"&quote_currency={usd}"

    # 2. Placed, acknowledged, filled at OANDA, and recorded with its value date.
    order, placement = placed(account, eur, "10000", "buy-10000", quoted)
    held = until(90, placement_state(account, order, placement, "PLACEMENT_STATE_WORKING",
                                     "PLACEMENT_STATE_FILLED"),
                 "step 2, never acknowledged")
    must(held["venue_order_id"], f"step 2, OANDA's order ID: {held}")
    until(60, lambda: [o for o in oanda_orders() if o[0] == placement and o[1] == "FILLED"],
          "step 2, the synthetic OANDA never filled it")
    until(90, placement_state(account, order, placement, "PLACEMENT_STATE_FILLED"),
          "step 2, the fill was not recorded with its value date")
    first = until(30, lambda: pending(book()), "step 2, EUR not pending to a value date")
    must(list(first.values()) == [Decimal(10000)], f"step 2, 10,000 EUR pending: {first}")
    (value_date,) = first

    # 3. A market order, naming its quote currency: sent and filled at once.
    order3, placement3 = placed(account, eur, "5000", "market-5000",
                                f"{quoted}&type=market&tif=fok")
    until(60, placement_state(account, order3, placement3, "PLACEMENT_STATE_FILLED"),
          "step 3, the market order was not filled")
    must(len(sent_once(placement3)) == 1, "step 3, sent to OANDA once")
    def fifteen_pending():
        held = pending(book())
        return held if held.get(value_date) == 15000 else None

    both = until(30, fifteen_pending, "step 3, EUR's 15,000 not pending to the value date")

    # 4. An order taken in OANDA's own app: its fill on no placement.
    ask_oanda({"order": {"instrument": "EUR_USD", "units": "2000"}})

    def unmatched():
        held = desk("write", f"/unmatched?account={account}")["executions"]
        return held or None

    (outside,) = until(90, unmatched, "step 4, the app's fill was not reported on no placement")
    must(outside["instrument_id"] == eur and outside["side"] == "SIDE_BUY"
         and Decimal(outside["quantity"]) == 2000 and outside["venue_order_id"],
         f"step 4, the app's fill: {outside}")
    must(pending(book()) == both, "step 4, an unmatched fill moves nothing")

    # 5. OANDA's 17:00 New York roll.
    ask_oanda({"roll": True})

    def rolled():
        lines = book()
        held = pending(lines)
        return (held, lines) if entries(lines, "value-date-rolled") and value_date not in held \
            else None

    after, lines = until(90, rolled, "step 5, the roll did not move EUR's pending lines")
    must(len(after) == 1 and list(after.values()) == [Decimal(15000)],
         f"step 5, EUR's 15,000 rolled to one later date: {after}")
    (rolled_to,) = after
    must(rolled_to > value_date, f"step 5, rolled forward: {value_date} to {rolled_to}")

    # 6. The desk accepts the app's fill: charged again, and caught up.
    desk("write", f"/accept?executions={outside['execution_id']}")

    def caught_up():
        lines = book()
        held = pending(lines)
        done = held == {rolled_to: Decimal(17000)} and len(entries(lines, "value-date-rolled")) == 2
        return lines if done else None

    lines = until(90, caught_up, "step 6, the accepted fill was not caught up to the rolled date")
    must(desk("write", f"/unmatched?account={account}")["executions"] == [],
         "step 6, nothing left unmatched")
    accepted = [o for o in desk("write", f"/orders?account={account}")["orders"]
                if any(p["venue_order_id"] == outside["venue_order_id"] for p in o["placements"])]
    must(accepted and accepted[0]["state"] == "ORDER_STATE_FILLED",
         f"step 6, the accept made a filled order: {accepted}")

    # 7. A replace, a replace not offered, and a cancel the desk asks for.
    order4, placement4 = placed(account, eur, WORKS, "replace-1m", quoted)
    working = until(60, placement_state(account, order4, placement4, "PLACEMENT_STATE_WORKING"),
                    "step 7, not working")
    first_id = working["venue_order_id"]
    desk("write", f"/replace?placement={placement4}&limit=1.07500")

    def replaced():
        held = placement_of(order_of(account, order4), placement4)
        done = held["changes"] == [["PLACEMENT_CHANGE_KIND_REPLACE", "CHANGE_REQUEST_OUTCOME_APPLIED"]]
        return held if done else None

    held4 = until(60, replaced, "step 7, the replace was not applied")
    must(held4["venue_order_id"] not in ("", first_id), f"step 7, OANDA's new order: {held4}")
    desk("write", f"/replace?placement={placement4}&tif=opg")

    def refused():
        held = placement_of(order_of(account, order4), placement4)
        last = held["changes"][-1:] == [["PLACEMENT_CHANGE_KIND_REPLACE", "CHANGE_REQUEST_OUTCOME_REJECTED"]]
        return held if last and held["state"] == "PLACEMENT_STATE_WORKING" else None

    until(60, refused, "step 7, the replace not offered was not refused, working on")
    desk("write", f"/cancel?placement={placement4}")
    until(60, placement_state(account, order4, placement4, "PLACEMENT_STATE_CANCELLED"),
          "step 7, not cancelled")

    # 8. A pause, then a stop.
    runner("instruction", "pause", "--plugin", "oanda", "note=Hold OANDA's sends.")
    time.sleep(3)
    order5, placement5 = placed(account, eur, WORKS, "paused-1m", quoted)
    time.sleep(6)
    held5 = placement_of(order_of(account, order5), placement5)
    must(held5["state"] == "PLACEMENT_STATE_QUEUED", f"step 8, held under the pause: {held5}")
    must(not sent_once(placement5), "step 8, nothing sent to OANDA under the pause")
    runner("instruction", "active", "--plugin", "oanda", "note=Resume.")
    until(90, placement_state(account, order5, placement5, "PLACEMENT_STATE_WORKING"),
          "step 8, the held placement was not sent once the pause lifted")
    runner("instruction", "stop", "--plugin", "oanda", "note=Halt OANDA; cancel its work.")
    until(60, placement_state(account, order5, placement5, "PLACEMENT_STATE_CANCELLED"),
          "step 8, the stop's cancel was not recorded")
    runner("instruction", "active", "--plugin", "oanda", "note=Resume.")

    # 9. Restarted with a placement working.
    order7, placement7 = placed(account, eur, WORKS, "restart-1m", quoted)
    until(60, placement_state(account, order7, placement7, "PLACEMENT_STATE_WORKING"),
          "step 9, not working before the restart")
    compose("up", "-d", "--no-deps", "--force-recreate", "oanda")
    runner("ready", "--instance", "oanda")
    time.sleep(8)
    must(len(sent_once(placement7)) == 1, "step 9, a second order sent after the restart")
    desk("write", f"/cancel?placement={placement7}")
    until(90, placement_state(account, order7, placement7, "PLACEMENT_STATE_CANCELLED"),
          "step 9, the restarted plugin did not cancel its order at OANDA")

    # 10. The book.
    lines = book()
    for wanted in ("order|E2E Orders|", "placement|E2E Orders|", "execution|E2E Orders|"):
        must(any(line.startswith(wanted) for line in lines), f"step 10, store book prints {wanted!r}")
    executions = [line for line in lines if line.startswith("execution|E2E Orders|")]
    must(pending(lines) == {rolled_to: Decimal(17000)},
         f"step 10, EUR's 17,000 pending to {rolled_to}: {pending(lines)}")
    print(
        f"orders OK: trading enabled on the connection; the desk's buy of 10,000 EUR at 1.08000 "
        f"USD acknowledged ({held['venue_order_id']}), filled at the synthetic OANDA as EUR_USD "
        f"and pending to its value date {value_date}, the date read's; a market buy of 5,000 "
        f"naming USD as its quote currency filled at once; an order taken in OANDA's own app "
        f"reported on no placement and held unmatched; OANDA's 17:00 New York roll charging "
        f"each trade's financing and rolling EUR's 15,000 from {value_date} to {rolled_to}; the "
        f"app's fill accepted by the desk, charged again on its placement and caught up to "
        f"{rolled_to}, 17,000 pending there in {len(executions)} executions; a replace at OANDA "
        f"to its new order ({held4['venue_order_id']}), one not offered refused unsupported, and "
        f"a cancel; a placement held under a pause and sent when it lifted; a stop's cancel at "
        f"once; a restart sending no second order and cancelling at OANDA; "
        f"{len(oanda_orders())} orders at the synthetic OANDA in all"
    )


def main():
    open(LOG, "w").close()
    try:
        run()
    except (Failed, subprocess.TimeoutExpired, KeyError, ValueError, IndexError) as failed:
        print(f"orders FAILED: {failed}; the run is in {LOG}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
