"""OANDA's v20 API behind a small interface: the one module that speaks HTTP
to OANDA, and the shape synthetic.py stands in for.

`Venue` is what the plugin asks of OANDA: the accounts the token reaches, an
account with its open trades and positions, its tradeable instruments, its
transactions since an ID, the transaction stream, and an order placed, read,
cancelled or replaced; and from 0.5.0, for the data role, the account's
prices now, its daily candles and its pricing stream
(https://developer.oanda.com/rest-live-v20/pricing-ep/). `Oanda` answers it
from OANDA's hosts, practice or live as the `environment` setting says
(https://developer.oanda.com/rest-live-v20/introduction/), with the
personal access token as a Bearer token and nothing else of the deployment's
(https://developer.oanda.com/rest-live-v20/authentication/). It uses the
standard library alone, each call in a thread so the plugin's event loop
never waits on one.

**The environment its session reached** (contract v26, G1 question 5):
`environment` is read from the host it calls, `api-fxpractice.oanda.com`
`paper` and `api-fxtrade.oanda.com` `live`
(`meridian.reached_environment`), and is published on the venue catalogue
(trading.py): the second key of the agents' demo-only guard, which counts a
connection demo only while the setting and this both say `paper`.

**SAFETY.** Nothing in this repository's tests or `make e2e` constructs
`Oanda`: they trade and read the synthetic OANDA only (synthetic.py). A
practice or live run is the product owner's, by hand. The tests of this
module hand it an opener that records the request and answers it in
process; none reaches a network.

**What a failure says.** A `VenueError` names what was asked, the HTTP
status and OANDA's own `errorCode`, never the token (it is in a header,
never a URL) and never a header. `answered` tells a refusal OANDA answered
(400, 401, 403, 404) from a call it never answered (no status), which the
plugin resolves by asking, never by sending again (the order-path plan's
Q17). Every number OANDA sends is a decimal string, read as a `Decimal`
where it is used, never a float: the JSON is parsed with `parse_float`
Decimal for the few numbers that are not strings.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator, Callable, Sequence
from decimal import Decimal
from typing import Any, Protocol

import meridian

Json = dict[str, Any]

#: OANDA's REST and stream hosts, by environment (the development guide).
HOSTS = {
    "practice": ("https://api-fxpractice.oanda.com", "https://stream-fxpractice.oanda.com"),
    "live": ("https://api-fxtrade.oanda.com", "https://stream-fxtrade.oanda.com"),
}
#: The REST hosts by the environment a session reaching them reports.
LIVE_HOSTS = ("api-fxtrade.oanda.com",)
PRACTICE_HOSTS = ("api-fxpractice.oanda.com",)

#: The most candles OANDA answers one request with (its `count`'s bound).
MOST_CANDLES = 5000

#: How long one REST call may take, and how long the stream may stay silent
#: (OANDA sends a heartbeat every five seconds) before it is taken for lost.
CALL_SECONDS = 30.0
STREAM_SILENCE_SECONDS = 20.0

#: The most transactions `transactions_since` asks for in one page, OANDA's
#: own page size for an ID range.
PAGE = 1000


def as_json(value: Any) -> Json:
    """A JSON object OANDA sent, or an empty one where it sent anything else."""
    return value if isinstance(value, dict) else {}


def parse_exact(text: str | bytes) -> Any:
    """OANDA's JSON with every non-string number read exactly."""
    return json.loads(text, parse_float=Decimal)


class VenueError(Exception):
    """A call to OANDA that failed: what was asked, OANDA's HTTP status where
    it answered, and its body where it sent one (its `errorCode`,
    `errorMessage` and any reject transaction). Never a credential."""

    def __init__(self, asked: str, status: int | None = None, said: Json | None = None) -> None:
        self.asked = asked
        self.status = status
        self.said: Json = said or {}
        code = str(self.said.get("errorCode") or "")
        where = f"HTTP {status}" if status is not None else "no answer"
        super().__init__(f"{asked}: {where}" + (f", {code}" if code else ""))

    @property
    def answered(self) -> bool:
        return self.status is not None

    @property
    def unauthorised(self) -> bool:
        return self.status == 401


class Venue(abc.ABC):
    """What the plugin asks of OANDA (above)."""

    #: The environment the session reached, read from the host it calls:
    #: `live`, `paper`, or empty where it is neither of OANDA's hosts.
    environment: str = ""

    @abc.abstractmethod
    async def accounts(self) -> list[Json]:
        """GET /v3/accounts: the accounts the token reaches."""

    @abc.abstractmethod
    async def account(self, account_id: str) -> Json:
        """GET /v3/accounts/{accountID}: `account`, with its trades, positions
        and orders, and `lastTransactionID`."""

    @abc.abstractmethod
    async def instruments(self, account_id: str) -> list[Json]:
        """GET /v3/accounts/{accountID}/instruments: the instruments the
        account may trade, each with its precision and units."""

    @abc.abstractmethod
    async def transactions_since(
        self, account_id: str, since_id: str
    ) -> tuple[list[Json], str]:
        """GET /v3/accounts/{accountID}/transactions/sinceid: each transaction
        after `since_id`, oldest first, and the account's last transaction
        ID."""

    @abc.abstractmethod
    async def place(self, account_id: str, body: Json) -> Json:
        """POST /v3/accounts/{accountID}/orders."""

    @abc.abstractmethod
    async def order(self, account_id: str, specifier: str) -> Json:
        """GET /v3/accounts/{accountID}/orders/{orderSpecifier}: by OANDA's
        order ID, or by the client's as `@<id>`."""

    @abc.abstractmethod
    async def cancel(self, account_id: str, specifier: str) -> Json:
        """PUT /v3/accounts/{accountID}/orders/{orderSpecifier}/cancel."""

    @abc.abstractmethod
    async def replace(self, account_id: str, specifier: str, body: Json) -> Json:
        """PUT /v3/accounts/{accountID}/orders/{orderSpecifier}: the order
        cancelled and a new one in its place."""

    @abc.abstractmethod
    def stream(self, account_id: str) -> AsyncIterator[Json]:
        """GET /v3/accounts/{accountID}/transactions/stream: each transaction
        as it happens, and a heartbeat (`type` HEARTBEAT) every few
        seconds; it ends, or raises VenueError, when the connection does."""

    @abc.abstractmethod
    async def pricing(self, account_id: str, instruments: Sequence[str]) -> Json:
        """GET /v3/accounts/{accountID}/pricing: each instrument's price now
        (`prices`, each a PRICE: its `time`, `bids` and `asks` with their
        `liquidity`, `tradeable`), and the answer's `time`."""

    @abc.abstractmethod
    async def candles(
        self, account_id: str, instrument: str, from_time: str, to_time: str
    ) -> list[Json]:
        """GET /v3/accounts/{accountID}/instruments/{instrument}/candles: the
        instrument's daily mid candles (`price` M, `granularity` D) from
        `from_time` to `to_time`, RFC 3339, oldest first; each day ends at
        17:00 New York, OANDA's default alignment, and the day still forming
        is `complete` false."""

    @abc.abstractmethod
    def pricing_stream(
        self, account_id: str, instruments: Sequence[str]
    ) -> AsyncIterator[Json]:
        """GET /v3/accounts/{accountID}/pricing/stream: each instrument's
        price as it changes, starting from its price now, and a heartbeat
        (`type` HEARTBEAT) every five seconds; it ends, or raises
        VenueError, when the connection does."""


class _Response(Protocol):
    status: int

    def read(self, amt: int = -1) -> bytes: ...

    def readline(self, limit: int = -1) -> bytes: ...

    def close(self) -> None: ...


Opener = Callable[[urllib.request.Request, float], _Response]


def _open(request: urllib.request.Request, timeout: float) -> _Response:
    answer: _Response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
    return answer


class Oanda(Venue):
    """OANDA's v20 REST and stream hosts, for one environment and token."""

    def __init__(self, token: str, environment: str, opener: Opener = _open) -> None:
        if environment not in HOSTS:
            raise ValueError(f"no OANDA environment {environment!r}")
        self._token = token
        self._rest, self._streams = HOSTS[environment]
        self._open = opener
        self.environment = meridian.reached_environment(
            self._rest, live=LIVE_HOSTS, paper=PRACTICE_HOSTS
        )

    def __repr__(self) -> str:
        return f"Oanda({self._rest})"

    def _request(
        self,
        method: str,
        base: str,
        path: str,
        query: Json | None = None,
        body: Json | None = None,
    ) -> urllib.request.Request:
        url = base + path + (f"?{urllib.parse.urlencode(query)}" if query else "")
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept-Datetime-Format", "RFC3339")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        return request

    def _call_now(
        self, asked: str, method: str, path: str, query: Json | None, body: Json | None
    ) -> Json:
        request = self._request(method, self._rest, path, query, body)
        try:
            answer = self._open(request, CALL_SECONDS)
            try:
                text = answer.read()
            finally:
                answer.close()
        except urllib.error.HTTPError as failed:
            said: Json = {}
            with contextlib.suppress(ValueError, OSError):
                parsed = parse_exact(failed.read())
                said = parsed if isinstance(parsed, dict) else {}
            raise VenueError(asked, failed.code, said) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            # Never answered: the plugin asks, never sends again.
            raise VenueError(asked) from None
        try:
            parsed = parse_exact(text) if text else {}
        except ValueError:
            raise VenueError(asked, 200, {"errorCode": "UNREADABLE_ANSWER"}) from None
        return parsed if isinstance(parsed, dict) else {}

    async def _call(
        self,
        asked: str,
        method: str,
        path: str,
        query: Json | None = None,
        body: Json | None = None,
    ) -> Json:
        return await asyncio.to_thread(self._call_now, asked, method, path, query, body)

    async def accounts(self) -> list[Json]:
        said = await self._call("listing accounts", "GET", "/v3/accounts")
        return [a for a in said.get("accounts") or [] if isinstance(a, dict)]

    async def account(self, account_id: str) -> Json:
        return await self._call("reading the account", "GET", f"/v3/accounts/{_id(account_id)}")

    async def instruments(self, account_id: str) -> list[Json]:
        said = await self._call(
            "reading instruments", "GET", f"/v3/accounts/{_id(account_id)}/instruments"
        )
        return [i for i in said.get("instruments") or [] if isinstance(i, dict)]

    async def transactions_since(
        self, account_id: str, since_id: str
    ) -> tuple[list[Json], str]:
        found: list[Json] = []
        last = since_id
        while True:
            said = await self._call(
                "reading transactions",
                "GET",
                f"/v3/accounts/{_id(account_id)}/transactions/sinceid",
                {"id": last or "0"},
            )
            page = [t for t in said.get("transactions") or [] if isinstance(t, dict)]
            found.extend(page)
            account_last = str(said.get("lastTransactionID") or last)
            if not page or len(page) < PAGE:
                return found, account_last
            last = str(page[-1].get("id") or account_last)

    async def place(self, account_id: str, body: Json) -> Json:
        return await self._call(
            "placing an order", "POST", f"/v3/accounts/{_id(account_id)}/orders", body=body
        )

    async def order(self, account_id: str, specifier: str) -> Json:
        return await self._call(
            "reading an order",
            "GET",
            f"/v3/accounts/{_id(account_id)}/orders/{urllib.parse.quote(specifier, safe='@')}",
        )

    async def cancel(self, account_id: str, specifier: str) -> Json:
        return await self._call(
            "cancelling an order",
            "PUT",
            f"/v3/accounts/{_id(account_id)}/orders/"
            f"{urllib.parse.quote(specifier, safe='@')}/cancel",
        )

    async def replace(self, account_id: str, specifier: str, body: Json) -> Json:
        return await self._call(
            "replacing an order",
            "PUT",
            f"/v3/accounts/{_id(account_id)}/orders/{urllib.parse.quote(specifier, safe='@')}",
            body=body,
        )

    async def stream(self, account_id: str) -> AsyncIterator[Json]:
        """The transaction stream (`_lines`)."""
        request = self._request(
            "GET", self._streams, f"/v3/accounts/{_id(account_id)}/transactions/stream"
        )
        async for line in self._lines("streaming transactions", request):
            yield line

    async def pricing(self, account_id: str, instruments: Sequence[str]) -> Json:
        return await self._call(
            "reading prices",
            "GET",
            f"/v3/accounts/{_id(account_id)}/pricing",
            {"instruments": ",".join(instruments)},
        )

    async def candles(
        self, account_id: str, instrument: str, from_time: str, to_time: str
    ) -> list[Json]:
        said = await self._call(
            "reading candles",
            "GET",
            f"/v3/accounts/{_id(account_id)}/instruments/"
            f"{urllib.parse.quote(instrument, safe='_')}/candles",
            {"price": "M", "granularity": "D", "from": from_time, "to": to_time},
        )
        return [c for c in said.get("candles") or [] if isinstance(c, dict)]

    async def pricing_stream(
        self, account_id: str, instruments: Sequence[str]
    ) -> AsyncIterator[Json]:
        """The pricing stream (`_lines`), from each instrument's price now."""
        request = self._request(
            "GET",
            self._streams,
            f"/v3/accounts/{_id(account_id)}/pricing/stream",
            {"instruments": ",".join(instruments), "snapshot": "true"},
        )
        async for line in self._lines("streaming prices", request):
            yield line

    async def _lines(self, asked: str, request: urllib.request.Request) -> AsyncIterator[Json]:
        """A stream, read in a thread a line at a time and handed to the
        loop; a silence past STREAM_SILENCE_SECONDS, or the connection's end,
        ends it."""
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue[Json | BaseException | None] = asyncio.Queue()
        stop = threading.Event()

        def read() -> None:
            try:
                answer = self._open(request, STREAM_SILENCE_SECONDS)
                try:
                    while not stop.is_set():
                        line = answer.readline()
                        if not line:
                            break
                        with contextlib.suppress(ValueError):
                            parsed = parse_exact(line)
                            if isinstance(parsed, dict):
                                loop.call_soon_threadsafe(lines.put_nowait, parsed)
                finally:
                    answer.close()
                loop.call_soon_threadsafe(lines.put_nowait, None)
            except urllib.error.HTTPError as failed:
                loop.call_soon_threadsafe(lines.put_nowait, VenueError(asked, failed.code))
            except (urllib.error.URLError, OSError, TimeoutError):
                loop.call_soon_threadsafe(lines.put_nowait, VenueError(asked))

        reader = threading.Thread(target=read, name="oanda-stream", daemon=True)
        reader.start()
        try:
            while True:
                got = await lines.get()
                if got is None:
                    return
                if isinstance(got, BaseException):
                    raise got
                yield got
        finally:
            stop.set()


def _id(account_id: str) -> str:
    return urllib.parse.quote(account_id, safe="-")
