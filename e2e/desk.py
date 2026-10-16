"""The desk OANDA's `make e2e` places its orders from: a stand-in holding
`portfolio`, `oms` and `ems` on the Python SDK, and `reporting` to name a
currency's cash instrument by its ISO 4217 code (W3.1), run from the plugin's
own image (e2e/plugin.yaml), after SnapTrade's and Alpaca's. Each page answers JSON so
the harness's runner drives it (`page --level ... PATH`), acting for the
person the session's `Meridian-Caller` header names. SIMULATED ONLY: it
reaches the deployment through its sidecar and nothing else, and the orders
go to the synthetic OANDA.

A spot FX order is an order in the base currency's cash instrument, its
limit in the quote currency (plans/an-order-reaches-a-venue-through-the-book,
"Spot FX needs nothing more"), and from contract v23 it names the quote
currency's cash instrument (`quote_currency`, W11.1).

  /propose?account=&instrument=&quantity=[&tif=][&key=][&type=][&limit=][&quote=][&side=]
          [&quote_currency=]              ProposeOrder: a limit buy, or as given
  /release?order=                         ReleaseOrder (oms)
  /claim?order=                           the claimed working condition (ems)
  /place?order=&quantity=&code=[&limit=][&quote=]  CreatePlacement at OANDA's instance
  /cancel?placement=                      RequestPlacementChange, a cancel (ems)
  /replace?placement=[&limit=][&quote=][&quantity=][&tif=]  a replace (ems)
  /map?account=&code=                     MapVenueAccount on OANDA (ems's admin)
  /orders?account=                        ListOrders, a line each
  /unmatched?account=                     ListOrders' executions on no placement (contract v23)
  /accept?executions=                     AcceptExecution to a new order (contract v23, ems)
  /resolve?iso4217=                       ResolveIdentifier: a currency's cash instrument
"""

import asyncio
import http.server
import json
import threading
import urllib.parse
from decimal import Decimal

import meridian
from meridian import Interface, Page
from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops

PORT = 8000
LOOP = asyncio.new_event_loop()
CONNECTIVITY = "oanda"
TIMES = {
    "day": ops.TIME_IN_FORCE_DAY,
    "gtc": ops.TIME_IN_FORCE_GOOD_TILL_CANCELLED,
    "gtd": ops.TIME_IN_FORCE_GOOD_TILL_DATE,
    "fok": ops.TIME_IN_FORCE_FILL_OR_KILL,
    "opg": ops.TIME_IN_FORCE_AT_THE_OPEN,
}


def limit_of(query, default="1.08000"):
    """The limit price asked, in the quote currency asked (USD unless said);
    none for a market order."""
    said = one(query, "limit", default)
    return meridian.Money(Decimal(said), one(query, "quote", "USD")) if said else None


class Refusal(Exception):
    def __init__(self, said):
        super().__init__(json.dumps(said))
        self.said = said


def one(query, name, default=""):
    return query.get(name, [default])[0]


def order_json(order):
    q = order.status.quantities
    return {
        "order_id": order.order_id,
        "state": ops.OrderState.Name(order.status.state),
        "ordered": str(as_decimal(q.ordered)),
        "reported_filled": str(as_decimal(q.reported_filled)),
        "committed": str(as_decimal(q.committed)),
        "placements": [
            {
                "placement_id": p.placement_id,
                "state": ops.PlacementState.Name(p.status.state),
                "venue_order_id": p.venue_order_id,
                "venue_order_ids": list(p.venue_order_ids),
                "rejection": ops.RejectionReason.Name(p.rejection_reason),
                "quantity": str(as_decimal(p.quantity)),
                "limit": str(as_decimal(p.limit_price.amount)) if p.HasField("limit_price") else "",
                "changes": [
                    [
                        ops.PlacementChangeKind.Name(c.placement_change.kind),
                        ops.ChangeRequestOutcome.Name(c.outcome),
                    ]
                    for c in p.change_requests
                ],
            }
            for p in order.placements
        ],
    }


class Desk:
    def __init__(self, plugin):
        self.plugin = plugin

    async def call(self, operation, **params):
        try:
            return await getattr(self.plugin, operation)(**params)
        except meridian.MeridianError as failed:
            said = {"words": str(failed), "type": type(failed).__name__}
            raise Refusal({"refused": said}) from None

    async def answer(self, path, query, caller):
        order = one(query, "order")
        if path == "/propose":
            tif = TIMES[one(query, "tif", "gtc")]
            kind = one(query, "type", "limit")
            done = await self.call(
                "propose_order",
                account_id=one(query, "account"),
                instrument_id=one(query, "instrument"),
                side=one(query, "side", "buy"),
                quantity=Decimal(one(query, "quantity")),
                order_type=kind,
                limit_price=limit_of(query) if kind == "limit" else None,
                time_in_force=tif,
                expire_date="2026-12-31" if tif == ops.TIME_IN_FORCE_GOOD_TILL_DATE else "",
                quote_currency=one(query, "quote_currency"),
                idempotency_key=one(query, "key"),
                acting_for=caller,
            )
            return order_json(done.order)
        if path == "/release":
            done = await self.call("release_order", order_id=order, acting_for=caller)
            return order_json(done.order)
        if path == "/claim":
            done = await self.call(
                "set_working_condition",
                order_id=order,
                condition=ops.ORDER_CONDITION_CLAIMED,
                set=True,
                acting_for=caller,
            )
            return order_json(done.order)
        if path == "/place":
            done = await self.call(
                "create_placement",
                order_id=order,
                connectivity_instance_id=CONNECTIVITY,
                venue_account_code=one(query, "code"),
                quantity=Decimal(one(query, "quantity")),
                limit_price=limit_of(query, one(query, "limit", "")) if one(query, "limit") else None,
                acting_for=caller,
            )
            return {
                "placement_id": done.placement.placement_id,
                "state": ops.PlacementState.Name(done.placement.status.state),
            }
        if path == "/cancel":
            done = await self.call(
                "request_placement_change",
                placement_id=one(query, "placement"),
                change=meridian.PlacementChange(kind=ops.PLACEMENT_CHANGE_KIND_CANCEL),
                reason="cancelled by the desk",
                acting_for=caller,
            )
            return {"change_request_id": done.change_request_id}
        if path == "/replace":
            tif = one(query, "tif")
            done = await self.call(
                "request_placement_change",
                placement_id=one(query, "placement"),
                change=meridian.PlacementChange(
                    kind=ops.PLACEMENT_CHANGE_KIND_REPLACE,
                    quantity=Decimal(one(query, "quantity")) if one(query, "quantity") else None,
                    limit_price=limit_of(query, "") if one(query, "limit") else None,
                    time_in_force=TIMES[tif] if tif else None,
                ),
                reason="replaced by the desk",
                acting_for=caller,
            )
            return {"change_request_id": done.change_request_id}
        if path == "/map":
            await self.call(
                "map_venue_account",
                connectivity_instance_id=CONNECTIVITY,
                venue_account_code=one(query, "code"),
                account_id=one(query, "account"),
                acting_for=caller,
            )
            return {"mapped": one(query, "code")}
        if path == "/resolve":
            code = one(query, "iso4217")
            found = await self.call(
                "resolve_identifier",
                identifiers=[meridian.Identifier(scheme="iso4217", value=code)],
                stated_asset_class="cash",
                stated_currency=code,
            )
            return {"instrument_id": found.instrument_id}
        if path == "/orders":
            listed = await self.call("list_orders", account_id=one(query, "account"), page_size=500)
            return {"orders": [order_json(o) for o in listed.orders]}
        if path == "/unmatched":
            listed = await self.call(
                "list_orders",
                account_id=one(query, "account"),
                execution_state=ops.EXECUTION_STATE_UNMATCHED,
                page_size=500,
            )
            return {
                "executions": [
                    {
                        "execution_id": e.execution_id,
                        "venue_order_id": e.venue_order_id,
                        "instrument_id": e.instrument_id,
                        "side": ops.Side.Name(e.side),
                        "quantity": str(as_decimal(e.quantity)),
                        "settlement_date": e.computed_settlement_date or e.settlement_date,
                    }
                    for e in listed.executions
                    if not e.placement_id and e.state == ops.EXECUTION_STATE_UNMATCHED
                ]
            }
        if path == "/accept":
            done = await self.call(
                "accept_execution",
                execution_ids=one(query, "executions").split(","),
                new_order=ops.NewOrderFromExecutions(note="Taken in OANDA's own app"),
                note="Ours: the account holder's order at OANDA",
                acting_for=caller,
            )
            return {"accepted": len(one(query, "executions").split(",")), "said": str(done)[:200]}
        return {"page": "the desk"}


def serve(desk):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            caller = self.headers.get("Meridian-Caller") or None
            future = asyncio.run_coroutine_threadsafe(
                desk.answer(parsed.path, urllib.parse.parse_qs(parsed.query), caller), LOOP
            )
            try:
                body = json.dumps(future.result(timeout=60), sort_keys=True)
            except Refusal as failed:
                body = json.dumps(failed.said, sort_keys=True)
            except Exception as failed:  # said on the page, for the run to quote
                body = json.dumps({"failed": f"{type(failed).__name__}: {failed}"})
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


async def main():
    roles = ["portfolio", "oms", "ems", "reporting"]
    plugin = await meridian.connect(
        # Contract v25: what the stand-in registers is its declaration.
        declaration=meridian.Declaration(interface=Interface(
            PORT, "Desk", pages=[Page("/", "Desk", levels=["write", "admin"], roles=roles)]
        ))
    )
    print(f"DESK registered as {plugin.identity.instance_id}", flush=True)
    threading.Thread(target=serve, args=(Desk(plugin),), daemon=True).start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.set_event_loop(LOOP)
    LOOP.run_until_complete(main())
