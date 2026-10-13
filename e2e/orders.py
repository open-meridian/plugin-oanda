"""`make e2e`'s order path (contract v22), after the custody steps, in the
same harness: OANDA holding `custody` and `connectivity` in sample data, and
the desk (e2e/desk.py) holding `portfolio`, `oms` and `ems`. SIMULATED
ONLY: the synthetic OANDA (src/oanda/synthetic.py); no OANDA host, practice
or live, is reached.

1. Set up: an account for the orders, the desk's grants on it, EUR's and
   USD's cash instruments named; OANDA made available and linked to the
   desk, the account mapped to the synthetic account's code; trading
   enabled on the connection under the connectivity role's admin (a new
   connection is read-only); releases not waiting for compliance.
2. A buy of 10,000 EUR, limit 1.08000 USD: placed at OANDA as EUR_USD,
   acknowledged with OANDA's order ID, and filled there. Its value date is
   the date read's, the pair's spot date: the harness's platform holds no
   spot-date rule, so the fill waits, never sent with a guessed date -- the
   plugin says so, and the book holds no execution for it.
3. A market order, which names no counter currency in contract v22: rejected
   `unsupported`, nothing sent.
4. A replace and a cancel the desk asks for: a working placement of
   1,000,000 replaced at OANDA to a limit of 1.07500, OANDA's new order ID
   followed and the request applied; a replace to at the open, which OANDA
   has no time in force for, refused `unsupported`, the placement working
   on; then cancelled at OANDA, then in the book.
5. A pause: a placement held back, queued; lifted, it is sent. A stop: OANDA
   cancels it at once, the cancel reported naming the stop; lifted again.
6. OANDA restarted with a placement working: it reads the book again, sends
   no second order, and cancels it at OANDA when the desk asks.
7. `store book` prints the orders and placements.

Standard library only; the Makefile hands it the harness's compose command
as its arguments.
"""

import json
import subprocess
import sys
import time

COMPOSE = sys.argv[1:]
LOG = ".e2e/orders.log"
CODE = "101-001-0000000-001"
CONNECTION = f"synthetic:{CODE}"
WORKS = "1000000"


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


def sent_once(placement_id):
    return [o for o in oanda_orders() if o[0] == placement_id and not o[2]]


def run():
    # 1. Set up.
    account = runner("account", "E2E Orders", "--instance", "desk").strip().splitlines()[-1]
    for role in ("portfolio", "oms", "ems"):
        runner("grant", "--instance", "desk", "--level", "write", "--role", role, "--accounts", account)
    runner("grant", "--instance", "desk", "--level", "admin", "--role", "ems")
    runner("grant", "--instance", "oanda", "--level", "admin", "--role", "connectivity")
    eur = desk("write", "/resolve?iso4217=EUR")["instrument_id"]
    usd = desk("write", "/resolve?iso4217=USD")["instrument_id"]
    must(eur and usd, f"step 1, EUR's and USD's cash instruments: {eur}, {usd}")
    runner("connectivity", "available", "--instance", "oanda")
    runner("connectivity", "link", "--instance", "oanda", "--ems", "desk",
           "--user", "person:local|harness")
    mapped = desk("admin", f"/map?account={account}&code={CODE}")
    must(mapped == {"mapped": CODE}, f"step 1, mapped: {mapped}")
    runner("form", "--instance", "oanda", "--level", "admin", "--page", "/admin/connections",
           "--post", "/admin/connections/enable-trading", f"connection_id={CONNECTION}",
           "--expect", "Trading enabled on")
    runner("attribute", account, "waits=false", "note=No compliance plugin in this run.")

    # 2. Placed, acknowledged and filled at OANDA; the value date waits.
    order, placement = placed(account, eur, "10000", "buy-10000")
    held = until(90, placement_state(account, order, placement, "PLACEMENT_STATE_WORKING",
                                     "PLACEMENT_STATE_FILLED"),
                 "step 2, never acknowledged")
    must(held["venue_order_id"], f"step 2, OANDA's order ID: {held}")
    until(60, lambda: [o for o in oanda_orders() if o[0] == placement and o[1] == "FILLED"],
          "step 2, the synthetic OANDA never filled it")
    waiting = until(
        90,
        lambda: next((line for line in compose("logs", "--no-color", "oanda").stdout.splitlines()
                      if f"{placement}: no value date yet for fill" in line), None),
        "step 2, the fill neither reported nor said to wait for its value date",
    )
    book = compose("run", "--rm", "-T", "store", "book").stdout
    executions = [line for line in book.splitlines() if line.startswith("execution|E2E Orders|")]
    must(not executions, f"step 2, no execution with a guessed date: {executions}")
    outcome = waiting.split("(")[-1].split(")")[0]

    # 3. A market order: no counter currency in contract v22.
    order3, placement3 = placed(account, eur, "10000", "market-10000", "&type=market&tif=fok")
    rejected = until(60, placement_state(account, order3, placement3, "PLACEMENT_STATE_REJECTED"),
                     "step 3, not rejected")
    must(rejected["rejection"] == "REJECTION_REASON_UNSUPPORTED", f"step 3: {rejected}")
    must(not sent_once(placement3), "step 3, nothing sent to OANDA")

    # 4. A replace, a replace not offered, and a cancel the desk asks for.
    order4, placement4 = placed(account, eur, WORKS, "replace-1m")
    working = until(60, placement_state(account, order4, placement4, "PLACEMENT_STATE_WORKING"),
                    "step 4, not working")
    first_id = working["venue_order_id"]
    desk("write", f"/replace?placement={placement4}&limit=1.07500")

    def replaced():
        held = placement_of(order_of(account, order4), placement4)
        done = held["changes"] == [["PLACEMENT_CHANGE_KIND_REPLACE", "CHANGE_REQUEST_OUTCOME_APPLIED"]]
        return held if done else None

    held4 = until(60, replaced, "step 4, the replace was not applied")
    must(held4["venue_order_id"] not in ("", first_id), f"step 4, OANDA's new order: {held4}")
    desk("write", f"/replace?placement={placement4}&tif=opg")

    def refused():
        held = placement_of(order_of(account, order4), placement4)
        last = held["changes"][-1:] == [["PLACEMENT_CHANGE_KIND_REPLACE", "CHANGE_REQUEST_OUTCOME_REJECTED"]]
        return held if last and held["state"] == "PLACEMENT_STATE_WORKING" else None

    until(60, refused, "step 4, the replace not offered was not refused, working on")
    desk("write", f"/cancel?placement={placement4}")
    until(60, placement_state(account, order4, placement4, "PLACEMENT_STATE_CANCELLED"),
          "step 4, not cancelled")

    # 5. A pause, then a stop.
    runner("instruction", "pause", "--plugin", "oanda", "note=Hold OANDA's sends.")
    time.sleep(3)
    order5, placement5 = placed(account, eur, WORKS, "paused-1m")
    time.sleep(6)
    held5 = placement_of(order_of(account, order5), placement5)
    must(held5["state"] == "PLACEMENT_STATE_QUEUED", f"step 5, held under the pause: {held5}")
    must(not sent_once(placement5), "step 5, nothing sent to OANDA under the pause")
    runner("instruction", "active", "--plugin", "oanda", "note=Resume.")
    until(90, placement_state(account, order5, placement5, "PLACEMENT_STATE_WORKING"),
          "step 5, the held placement was not sent once the pause lifted")
    runner("instruction", "stop", "--plugin", "oanda", "note=Halt OANDA; cancel its work.")
    until(60, placement_state(account, order5, placement5, "PLACEMENT_STATE_CANCELLED"),
          "step 5, the stop's cancel was not recorded")
    runner("instruction", "active", "--plugin", "oanda", "note=Resume.")

    # 6. Restarted with a placement working.
    order7, placement7 = placed(account, eur, WORKS, "restart-1m")
    until(60, placement_state(account, order7, placement7, "PLACEMENT_STATE_WORKING"),
          "step 6, not working before the restart")
    compose("up", "-d", "--no-deps", "--force-recreate", "oanda")
    runner("ready", "--instance", "oanda")
    time.sleep(8)
    must(len(sent_once(placement7)) == 1, "step 6, a second order sent after the restart")
    desk("write", f"/cancel?placement={placement7}")
    until(90, placement_state(account, order7, placement7, "PLACEMENT_STATE_CANCELLED"),
          "step 6, the restarted plugin did not cancel its order at OANDA")

    # 7. The book.
    book = compose("run", "--rm", "-T", "store", "book").stdout
    for wanted in ("order|E2E Orders|", "placement|E2E Orders|"):
        must(wanted in book, f"step 7, store book prints {wanted!r}")
    print(
        f"orders OK: trading enabled on the connection; the desk's buy of 10,000 EUR at 1.08000 "
        f"USD acknowledged ({held['venue_order_id']}) and filled at the synthetic OANDA as "
        f"EUR_USD, its fill waiting for its value date (the harness's date read answered "
        f"{outcome}: no spot-date rule held), no execution guessed; a market order rejected "
        f"unsupported, naming no counter currency; a replace at OANDA to its new order "
        f"({held4['venue_order_id']}), one not offered refused unsupported, and a cancel; a "
        f"placement held under a pause and sent when it lifted; a stop's cancel at once; a "
        f"restart sending no second order and cancelling at OANDA; {len(oanda_orders())} "
        f"orders at the synthetic OANDA in all"
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
