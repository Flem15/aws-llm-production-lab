"""Two brokers with the same interface:
  SimBroker    – backtests. Prices options with Black-Scholes off the real/CSV stock bars.
  AlpacaBroker – paper trading through Alpaca's API.
"""
from __future__ import annotations
import logging
import os
import time as _time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .errors import SafetyHalt
from .options import Contract, occ_symbol, add_trading_days, target_strike, bs_price

ET = ZoneInfo("America/New_York")
log = logging.getLogger("broker")
MINUTES_PER_DAY = 390
YEAR_MINUTES = 252 * MINUTES_PER_DAY


# ───────────────────────────── simulator ─────────────────────────────
class SimBroker:
    def __init__(self, iv: dict, default_iv=0.20, slippage_pct=0.02, fee_per_contract=0.05):
        self.iv, self.default_iv = iv or {}, default_iv
        self.slip, self.fee = slippage_pct, fee_per_contract
        self._now: datetime | None = None
        self._px: dict[str, float] = {}

    def set_market(self, now: datetime, prices: dict[str, float]):
        self._now = now
        self._px.update(prices)

    def now(self) -> datetime:
        return self._now

    def underlying_price(self, symbol: str) -> float:
        return self._px[symbol]

    def pick_contract(self, symbol, kind, price, dte, otm_steps) -> Contract | None:
        expiry = add_trading_days(self._now.date(), dte)
        k = target_strike(price, kind, otm_steps)
        return Contract(occ_symbol(symbol, expiry, kind, k), symbol, kind, k, expiry)

    def _years_left(self, c: Contract) -> float:
        close = datetime.combine(self._now.date(), datetime.min.time(), ET).replace(hour=16)
        mins_today = max(0.0, (close - self._now).total_seconds() / 60)
        extra_days, d = 0, self._now.date()
        while d < c.expiry:
            d = add_trading_days(d, 1)
            extra_days += 1
        return (mins_today + extra_days * MINUTES_PER_DAY) / YEAR_MINUTES

    def option_mid(self, c: Contract) -> float:
        S = self._px[c.underlying]
        sigma = self.iv.get(c.underlying, self.default_iv)
        return max(0.01, bs_price(S, c.strike, self._years_left(c), sigma, c.kind))

    def buy(self, c: Contract, qty: int) -> tuple[float, float]:
        return round(self.option_mid(c) * (1 + self.slip), 2), self.fee * qty

    def sell(self, c: Contract, qty: int) -> tuple[float, float]:
        return max(0.01, round(self.option_mid(c) * (1 - self.slip), 2)), self.fee * qty


# ───────────────────────────── alpaca ─────────────────────────────
class AlpacaBroker:
    TERMINAL = {"filled", "canceled", "cancelled", "rejected", "expired"}

    def __init__(self, paper: bool = True, fill_timeout: int = 20, ownership_guard=None):
        if not paper or os.getenv("ALLOW_LIVE_TRADING"):
            raise SafetyHalt("AlpacaBroker permits only paper trading")

        from .transport import proxy_configuration, configure_transport
        proxy = proxy_configuration()

        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient

        key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise SystemExit("Set ALPACA_API_KEY and ALPACA_SECRET_KEY (see .env.example)")
        self.journal = None
        self.worker_name = None
        self.ownership_guard = ownership_guard
        self.paper = paper
        self.fill_timeout = fill_timeout
        self.last_filled_qty = None
        self.trading = TradingClient(key, secret, paper=True)
        self.stocks = StockHistoricalDataClient(key, secret)
        self.opts = OptionHistoricalDataClient(key, secret)
        for client in (self.trading, self.stocks, self.opts):
            configure_transport(client, proxy)
        feed = os.getenv("ALPACA_DATA_FEED", "iex").lower()
        from alpaca.data.enums import DataFeed
        self.feed = DataFeed.SIP if feed == "sip" else DataFeed.IEX
        log.info("Alpaca paper client initialized (%s feed)", feed.upper())

    def _assert_ownership(self):
        guard = getattr(self, "ownership_guard", None)
        if guard is not None:
            guard()
        if os.getenv("BOT_CITY_TRADING_ENABLED", "false") != "true":
            raise SafetyHalt("paper trading is disabled pending supervised validation")

    def now(self) -> datetime:
        return datetime.now(ET)

    def clock(self):
        return self.trading.get_clock()

    def minute_bars(self, symbol: str, days_back: int = 4) -> pd.DataFrame:
        """Recent closed 1-min bars (several days so ATR/EMAs are warmed up)."""
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute,
                               start=self.now() - timedelta(days=days_back), feed=self.feed)
        df = self.stocks.get_stock_bars(req).df
        if df.empty:
            return df
        df = df.xs(symbol, level=0) if isinstance(df.index, pd.MultiIndex) else df
        df.index = pd.to_datetime(df.index).tz_convert(ET)
        df = df.between_time("09:30", "15:59")[["open", "high", "low", "close", "volume"]]
        cutoff = self.now().replace(second=0, microsecond=0)
        return df[df.index < cutoff]

    def underlying_price(self, symbol: str) -> float:
        from alpaca.data.requests import StockLatestTradeRequest
        t = self.stocks.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self.feed))
        return float(t[symbol].price)

    def pick_contract(self, symbol, kind, price, dte, otm_steps) -> Contract | None:
        from alpaca.trading.requests import GetOptionContractsRequest
        from alpaca.trading.enums import ContractType, AssetStatus
        today = self.now().date()
        want = target_strike(price, kind, otm_steps)
        req = GetOptionContractsRequest(
            underlying_symbols=[symbol], status=AssetStatus.ACTIVE,
            type=ContractType.CALL if kind == "C" else ContractType.PUT,
            expiration_date_gte=add_trading_days(today, dte) if dte else today,
            expiration_date_lte=add_trading_days(today, dte + 5),
            strike_price_gte=str(want - 5), strike_price_lte=str(want + 5), limit=500)
        chain = self.trading.get_option_contracts(req).option_contracts or []
        if not chain:
            log.warning("no %s contracts found for %s", kind, symbol)
            return None
        first_exp = min(c.expiration_date for c in chain)
        best = min((c for c in chain if c.expiration_date == first_exp),
                   key=lambda c: abs(float(c.strike_price) - want))
        exp = best.expiration_date if isinstance(best.expiration_date, date) else date.fromisoformat(str(best.expiration_date))
        return Contract(best.symbol, symbol, kind, float(best.strike_price), exp)

    def option_mid(self, c: Contract) -> float:
        from alpaca.data.requests import OptionLatestQuoteRequest
        from alpaca.data.enums import OptionsFeed
        feed = OptionsFeed.OPRA if os.getenv("ALPACA_OPTIONS_FEED", "indicative").lower() == "opra" else OptionsFeed.INDICATIVE
        q = self.opts.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=c.symbol, feed=feed))[c.symbol]
        bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return max(bid, ask)

    @staticmethod
    def _order_status(order) -> str:
        return str(getattr(order.status, "value", order.status)).lower()

    @staticmethod
    def _filled_qty(order) -> int:
        try:
            return int(float(order.filled_qty or 0))
        except (TypeError, ValueError):
            return 0

    def _recover_submitted_order(self, client_order_id: str):
        try:
            return self.trading.get_order_by_client_id(client_order_id)
        except Exception as exc:
            from .errors import OrderStateUnknown
            raise OrderStateUnknown(
                f"submit outcome unknown and client order {client_order_id} could not be reconciled"
            ) from exc

    def _market_once(self, c: Contract, qty: int, side) -> tuple[float, int, float]:
        self._assert_ownership()
        if getattr(self, "journal", None) is None:
            raise SafetyHalt("durable reconciliation is required before broker submission")
        self.journal.assert_ready()
        client_order_id = self.journal.begin_order(c, qty, side, self.worker_name)
        try:
            return self._execute_order(c, qty, side, client_order_id)
        except Exception as exc:
            self.journal.halt(str(exc))
            raise SafetyHalt("order operation incomplete; operator recovery required") from exc

    def _record_order(self, client_id, order):
        self.journal.observe(client_id, self.normalize_order(order))

    def _execute_order(self, c, qty, side, client_order_id):
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import TimeInForce
        from .errors import OrderStateUnknown

        request = MarketOrderRequest(
            symbol=c.symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        self._assert_ownership()
        try:
            order = self.trading.submit_order(request)
        except Exception:
            order = self._recover_submitted_order(client_order_id)

        self._record_order(client_order_id, order)
        deadline = _time.monotonic() + self.fill_timeout
        latest = order
        while _time.monotonic() < deadline:
            latest = self.trading.get_order_by_id(order.id)
            self._record_order(client_order_id, latest)
            status = self._order_status(latest)
            filled = self._filled_qty(latest)
            if status == "filled" and latest.filled_avg_price:
                return float(latest.filled_avg_price), filled or qty, 0.0
            if status in self.TERMINAL:
                if filled > 0 and latest.filled_avg_price:
                    return float(latest.filled_avg_price), filled, 0.0
                raise RuntimeError(f"order {status}: {c.symbol}")
            _time.sleep(0.5)

        self._assert_ownership()
        self.journal.cancellation(client_order_id)
        self._assert_ownership()
        try:
            self.trading.cancel_order_by_id(order.id)
        except Exception:
            log.exception("cancel request failed for Alpaca order %s", order.id)

        cancel_deadline = _time.monotonic() + 10
        while _time.monotonic() < cancel_deadline:
            latest = self.trading.get_order_by_id(order.id)
            self._record_order(client_order_id, latest)
            status = self._order_status(latest)
            filled = self._filled_qty(latest)
            if status == "filled" and latest.filled_avg_price:
                return float(latest.filled_avg_price), filled or qty, 0.0
            if status in self.TERMINAL:
                if filled > 0 and latest.filled_avg_price:
                    return float(latest.filled_avg_price), filled, 0.0
                raise RuntimeError(f"order canceled after fill timeout: {c.symbol}")
            _time.sleep(0.5)

        raise OrderStateUnknown(
            f"Alpaca order state remained non-terminal after cancel timeout: {c.symbol}"
        )

    def buy(self, c, qty):
        from alpaca.trading.enums import OrderSide
        price, filled, fees = self._market_once(c, qty, OrderSide.BUY)
        if filled < 1:
            raise RuntimeError(f"entry did not fill: {c.symbol}")
        self.last_filled_qty = filled
        if filled < qty:
            log.warning("partial Alpaca entry %s: %d of %d", c.symbol, filled, qty)
        return price, fees

    def sell(self, c, qty):
        from alpaca.trading.enums import OrderSide
        from .errors import OrderStateUnknown

        remaining = qty
        total_value = 0.0
        total_filled = 0
        for _ in range(3):
            price, filled, fees = self._market_once(c, remaining, OrderSide.SELL)
            total_value += price * filled
            total_filled += filled
            remaining -= filled
            if remaining <= 0:
                self.last_filled_qty = None
                return total_value / total_filled, fees
            log.warning("partial Alpaca exit %s: %d contracts remain", c.symbol, remaining)
        raise OrderStateUnknown(
            f"could not fully reconcile exit for {c.symbol}; {remaining} contracts may remain"
        )

    def cancel_all_open_orders(self):
        if self.journal is not None:
            raise SafetyHalt("startup recovery must not mutate broker orders")
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        from .errors import OrderStateUnknown

        open_orders = self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        if not open_orders:
            return
        self._assert_ownership()
        self.trading.cancel_orders()
        deadline = _time.monotonic() + 10
        while _time.monotonic() < deadline:
            remaining = self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
            if not remaining:
                return
            _time.sleep(0.5)
        raise OrderStateUnknown("open Alpaca orders remained after startup cancellation")

    def paper_account_id(self):
        return str(self.trading.get_account().id)

    @staticmethod
    def normalize_order(order):
        from decimal import Decimal
        from math import isfinite

        def integer(value):
            number = Decimal(str(value))
            if not number.is_finite() or number != number.to_integral_value() or number < 0:
                raise SafetyHalt("invalid broker order quantity")
            return int(number)

        asset_class = getattr(order, "asset_class", None)
        if str(getattr(asset_class, "value", asset_class)) != "us_option":
            raise SafetyHalt("unexpected broker order asset class")
        if not order.id or not order.client_order_id or not order.symbol:
            raise SafetyHalt("broker order identity is missing")
        qty, filled = integer(order.qty), integer(order.filled_qty)
        price = float(order.filled_avg_price) if order.filled_avg_price is not None else None
        status = AlpacaBroker._order_status(order)
        side = str(getattr(order.side, "value", order.side))
        if (qty <= 0 or filled > qty or side not in {"buy", "sell"} or
                (filled > 0 and (price is None or not isfinite(price) or price <= 0)) or
                (status == "filled" and filled != qty)):
            raise SafetyHalt("inconsistent broker fill response")
        return dict(client_order_id=str(order.client_order_id), broker_id=str(order.id),
                    symbol=str(order.symbol), side=side, qty=qty, filled_qty=filled,
                    filled_avg_price=price, status=status)

    def _order_history(self):
        """Overlap timestamp boundaries; never skip a full same-timestamp page."""
        from datetime import timedelta, timezone
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.common.enums import Sort
        observed, after = {}, None
        while True:
            page = self.trading.get_orders(GetOrdersRequest(
                status=QueryOrderStatus.ALL, limit=500, direction=Sort.ASC, after=after))
            if len(page) > 500:
                raise SafetyHalt("broker exceeded requested page size")
            previous, added = None, 0
            page_ids = set()
            for order in page:
                submitted = order.submitted_at
                if not submitted or not submitted.tzinfo:
                    raise SafetyHalt("broker history timestamp is missing or ambiguous")
                submitted = submitted.astimezone(timezone.utc)
                if (previous and submitted < previous) or (after and submitted < after):
                    raise SafetyHalt("broker history pagination order is invalid")
                previous = submitted
                execution = self.normalize_order(order)
                identity = execution["client_order_id"]
                if identity in page_ids:
                    raise SafetyHalt("duplicate broker order in history page")
                page_ids.add(identity)
                if identity in observed and observed[identity] != execution:
                    raise SafetyHalt("broker history changed during pagination")
                if identity not in observed:
                    added += 1
                observed[identity] = execution
            if len(page) < 500:
                return observed
            cursor = previous - timedelta(microseconds=1)
            if not added or (after and cursor <= after):
                raise SafetyHalt("broker timestamp boundary cannot be paginated completely")
            after = cursor

    def reconciliation_view(self):
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        import hashlib
        import json
        try:
            account_id = self.paper_account_id()
            positions = self.open_option_positions()
            if self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)):
                raise SafetyHalt("open broker orders require operator recovery")
            orders = self._order_history()
            # Full second pass detects concurrent fills/insertions. A checkpoint is
            # evidence, never a filter: old submitted orders may fill much later.
            if orders != self._order_history() or positions != self.open_option_positions():
                raise SafetyHalt("broker account changed during reconciliation")
            if self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)):
                raise SafetyHalt("open broker orders require operator recovery")
            digest = hashlib.sha256(json.dumps(orders, sort_keys=True).encode()).hexdigest()
            return dict(account_id=account_id, positions=positions,
                        orders=list(orders.values()),
                        history_checkpoint=dict(count=len(orders), sha256=digest,
                                                verified_at=self.now().isoformat()))
        except SafetyHalt:
            raise
        except Exception as exc:
            raise SafetyHalt("broker reconciliation could not be completed") from exc

    def open_option_positions(self) -> dict[str, int]:
        from decimal import Decimal
        positions = {}
        for position in self.trading.get_all_positions():
            asset_class = getattr(position, "asset_class", None)
            if str(getattr(asset_class, "value", asset_class)) != "us_option":
                raise SafetyHalt("unexpected broker position asset class")
            quantity = Decimal(str(position.qty))
            if (not quantity.is_finite() or quantity <= 0 or
                    quantity != quantity.to_integral_value() or
                    str(getattr(position.side, "value", position.side)) != "long" or
                    position.symbol in positions):
                raise SafetyHalt("unsupported, short, fractional or duplicate broker position")
            positions[position.symbol] = int(quantity)
        return positions

    def open_option_symbols(self) -> set[str]:
        return set(self.open_option_positions())


# ───────────────────────────── webull (hybrid) ─────────────────────────────
class WebullBroker(AlpacaBroker):
    """Hybrid: Alpaca supplies the clock, bars, option chain and quotes (free);
    Webull's official OpenAPI places the actual orders in your Webull account.

    Orders are marketable LIMIT orders (Webull options orders are limit-based):
      buy  at quote mid + `limit_buffer`, sell at mid − `limit_buffer`.
    If an entry doesn't fill in `fill_timeout` seconds it's cancelled (trade skipped).
    If an exit doesn't fill, it's re-priced lower and retried, so the bot never gets stuck in a trade.
    """

    SANDBOX = "api.sandbox.webull.com"
    PRODUCTION = "api.webull.com"

    def __init__(self, paper: bool = True, limit_buffer: float = 0.05, fill_timeout: int = 20):
        super().__init__(paper=True)            # Alpaca is only used for data here, so paper keys are fine
        from webull.core.client import ApiClient
        from webull.trade.trade_client import TradeClient

        key, secret = os.getenv("WEBULL_APP_KEY"), os.getenv("WEBULL_APP_SECRET")
        if not key or not secret:
            raise SystemExit("Set WEBULL_APP_KEY and WEBULL_APP_SECRET in .env (Webull → OpenAPI Management)")
        if not paper and os.getenv("ALLOW_LIVE_TRADING") != "yes":
            raise SystemExit("config says paper: false, but ALLOW_LIVE_TRADING=yes is not set. Refusing to trade real money.")
        region = os.getenv("WEBULL_REGION", "us")
        endpoint_override = os.getenv("WEBULL_ENDPOINT", "").strip().lower()
        if paper and endpoint_override and endpoint_override != self.SANDBOX:
            raise SystemExit("paper Webull mode only permits api.sandbox.webull.com")
        endpoint = endpoint_override or (self.SANDBOX if paper else self.PRODUCTION)
        api = ApiClient(key, secret, region)
        api.add_endpoint(region, endpoint)
        if os.getenv("WEBULL_TOKEN_DIR"):
            api.set_token_dir(os.getenv("WEBULL_TOKEN_DIR"))
        self.wb = TradeClient(api)
        self.wb_paper = paper
        self.account_id = os.getenv("WEBULL_ACCOUNT_ID") or self._first_account()
        self.limit_buffer, self.fill_timeout = limit_buffer, fill_timeout
        log.info("Webull orders → %s (%s), account %s", endpoint, "SANDBOX" if paper else "LIVE", self.account_id)

    # ── helpers ──
    @staticmethod
    def _json(res, what):
        if res is None or res.status_code != 200:
            body = getattr(res, "text", "") if res is not None else ""
            raise RuntimeError(f"Webull {what} failed: {getattr(res, 'status_code', '?')} {body[:300]}")
        return res.json()

    def _first_account(self) -> str:
        data = self._json(self.wb.account_v2.get_account_list(), "account list")
        accounts = data if isinstance(data, list) else data.get("accounts") or data.get("data") or []
        if not accounts:
            raise SystemExit(f"No Webull accounts returned: {data}")
        a = accounts[0]
        return str(a.get("account_id") or a.get("accountId") or a.get("id"))

    @staticmethod
    def _find(d, *keys):
        """First non-empty value for any of `keys`, searching nested dicts/lists."""
        if isinstance(d, dict):
            for k in keys:
                if d.get(k) not in (None, "", "0", 0):
                    return d[k]
            for v in d.values():
                r = WebullBroker._find(v, *keys)
                if r is not None:
                    return r
        elif isinstance(d, list):
            for v in d:
                r = WebullBroker._find(v, *keys)
                if r is not None:
                    return r
        return None

    def _order_payload(self, c: Contract, qty: int, side: str, limit: float, coid: str) -> list:
        return [{
            "client_order_id": coid,
            "combo_type": "NORMAL",
            "order_type": "LIMIT",
            "quantity": str(qty),
            "limit_price": f"{limit:.2f}",
            "option_strategy": "SINGLE",
            "side": side,
            "time_in_force": "DAY",
            "entrust_type": "QTY",
            "position_intent": "BUY_TO_OPEN" if side == "BUY" else "SELL_TO_CLOSE",
            "instrument_type": "OPTION",
            "market": "US",
            "symbol": c.underlying,
            "legs": [{
                "side": side,
                "quantity": str(qty),
                "symbol": c.underlying,
                "strike_price": f"{c.strike:g}",
                "option_expire_date": c.expiry.isoformat(),
                "instrument_type": "OPTION",
                "option_type": "CALL" if c.kind == "C" else "PUT",
                "market": "US",
            }],
        }]

    def _status(self, coid: str):
        d = self._json(self.wb.order_v3.get_order_detail(self.account_id, coid), "order detail")
        status = str(self._find(d, "status", "order_status") or "").upper().replace(" ", "_")
        price = self._find(d, "filled_price", "avg_filled_price", "filled_avg_price", "avg_price", "average_price")
        filled = self._find(d, "filled_quantity", "filled_qty", "cum_qty")
        return status, (float(price) if price else None), (int(float(filled)) if filled else 0), d

    def _limit_order(self, c: Contract, qty: int, side: str, limit: float) -> tuple[float, int]:
        """Place one limit order and wait. Returns (fill_price, filled_qty). Cancels any unfilled remainder."""
        import uuid
        coid = uuid.uuid4().hex
        self._json(self.wb.order_v3.place_order(self.account_id, self._order_payload(c, qty, side, limit, coid)),
                   f"{side} {c.symbol}")
        deadline = _time.time() + self.fill_timeout
        status, price, filled = "", None, 0
        while _time.time() < deadline:
            status, price, filled, _ = self._status(coid)
            if status == "FILLED":
                return (price or limit), (filled or qty)
            if status in ("CANCELLED", "FAILED", "REJECTED", "EXPIRED"):
                return (price or limit), filled
            _time.sleep(1)
        try:
            self.wb.order_v3.cancel_order(self.account_id, coid)
        except Exception:
            log.exception("cancel failed for %s", coid)
        _time.sleep(1)
        status, price, filled, _ = self._status(coid)
        return (price or limit), (qty if status == "FILLED" else filled)

    # ── broker interface ──
    def buy(self, c, qty):
        mid = self.option_mid(c)
        limit = round(mid * (1 + self.limit_buffer) + 0.01, 2)
        price, filled = self._limit_order(c, qty, "BUY", limit)
        if filled < 1:
            raise RuntimeError(f"entry not filled at {limit:.2f}: {c.symbol}")
        if filled < qty:
            log.warning("partial entry %s: %d of %d", c.symbol, filled, qty)
        self.last_filled_qty = filled
        return price, 0.0

    def sell(self, c, qty):
        remaining, proceeds, buffer = qty, 0.0, self.limit_buffer
        for attempt in range(6):                     # keep stepping the price down until we're out
            mid = self.option_mid(c)
            limit = max(0.01, round(mid * (1 - buffer) - 0.01, 2))
            price, filled = self._limit_order(c, remaining, "SELL", limit)
            proceeds += price * filled
            remaining -= filled
            if remaining <= 0:
                return round(proceeds / qty, 4), 0.0
            buffer = min(0.5, buffer * 2 + 0.05)
            log.warning("exit not fully filled (%d left), retrying at a lower price", remaining)
        raise RuntimeError(f"could not exit {c.symbol}: {remaining} contracts still open. CLOSE MANUALLY in Webull.")

    def open_option_symbols(self):
        """OCC symbols of open option positions, or None if the response can't be parsed
        (restore() then trusts the saved positions file instead)."""
        try:
            d = self._json(self.wb.account_v2.get_account_position(self.account_id), "positions")
        except Exception:
            log.exception("could not read Webull positions")
            return None
        if isinstance(d, list):
            rows = d
        else:
            key = next((k for k in ("holdings", "positions", "data") if isinstance(d.get(k), list)), None)
            if key is None:
                return None                      # unknown shape: don't guess "no positions"
            rows = d[key]
        out = set()
        for p in rows:
            legs = p.get("legs") or [p]
            for leg in legs:
                if str(leg.get("instrument_type", "")).upper() != "OPTION":
                    continue
                try:
                    exp = date.fromisoformat(str(leg.get("option_expire_date"))[:10])
                    kind = "C" if str(leg.get("option_type", "")).upper().startswith("C") else "P"
                    out.add(occ_symbol(leg.get("symbol") or leg.get("underlying_symbol"), exp, kind,
                                       float(leg.get("strike_price"))))
                except Exception:
                    return None
        return out
