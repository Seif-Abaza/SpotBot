"""Trading strategy engine: FLI signal evaluation and order execution."""

import json
import math
import random
import threading
import time

from spotbot.constants import (
    CONFIRM_MULTIPLIER,
    DEFAULT_SLIPPAGE,
    DEFAULT_TAKER_FEE,
    FLOAT_EPS,
    MACD_BUY_CONFIRM_EPS,
    MACD_SELL_CONFIRM_EPS,
    RSI_BUY_CONFIRM,
    RSI_BUY_THRESHOLD,
    RSI_SELL_CONFIRM,
    RSI_SELL_THRESHOLD,
    TRADE_HISTORY_LIMIT,
)
from spotbot.indicators import TradeSignal
from spotbot.exchange import ExchangeManager


def _normalize_trade_marker(result, fallback_ts=None):
    """Turn a trading-engine result into the marker payload expected by the chart."""
    if not isinstance(result, dict):
        return None

    action = result.get("action")
    if action not in ("buy", "sell", "pending"):
        return None

    ts = result.get("ts")
    if ts is None:
        ts = fallback_ts
    ts_val = None
    if ts is not None:
        try:
            ts_val = int(ts)
        except (TypeError, ValueError):
            ts_val = None

    price = result.get("price", result.get("close", 0))
    try:
        price = float(price)
    except (TypeError, ValueError):
        price = 0.0

    return {"ts": ts_val, "action": action, "price": price}


class TradingEngine:
    """
    Evaluates indicator signals with 1-candle confirmation before executing.
    Supports Fixed and Cumulative investment modes.
    """

    def __init__(self, exchange_mgr: ExchangeManager, logger: "TransactionLogger"):
        self.exch_mgr = exchange_mgr
        self.logger = logger
        self.pending_signal = None  # signal awaiting confirmation
        self.pending_trigger = None  # "rsi" | "macd" — which condition fired
        self.confirm_candle_ts = None  # timestamp of candle where signal appeared
        self.wallet_balance = 0.0
        self.accumulated_profit = 0.0  # cumulative mode: profits reinvested
        self.investment_mode = "FIXED"  # "FIXED" or "CUMULATIVE"
        self.investment_amount = 10.0  # base amount in USDT
        self.in_position = False
        # Code Review 3.4: position state has a SINGLE source of truth.
        # ``_entry_price`` / ``_position_qty`` are the canonical attrs;
        # ``entry_price`` / ``entry_qty`` are now @property proxies that
        # read/write the canonical ones.  This removes the manual-sync
        # duplication that produced PnL drift bugs (3.3-style).
        self._entry_price = 0.0
        self._position_qty = 0.0
        self._last_price = 0.0
        # Code Review 3.3: track the most recent exchange fill so callers
        # can use the actual filled price/qty (not the requested amount)
        # for PnL and wallet accounting.
        self._last_fill_price = 0.0
        self._last_fill_qty = 0.0
        self.accumulated_pnl = 0.0
        self.pair = ""
        self.ccxt = None
        # ── Trading gates (user must explicitly arm trading) ──
        self.trading_enabled = False  # global user toggle (Start Trading button)
        self._halted = False  # pause flag (halt/resume)
        # ── Lock to serialize evaluate_signal calls between the parallel
        # pipeline thread and the main-thread refresh callback. ──
        # Code Review High #6: RLock (reentrant) so that methods which themselves
        # call back into evaluate_signal (or are invoked from signal handlers
        # that already hold the lock) don't self-deadlock.  reset_position,
        # on_buy_signal, and on_sell_signal all mutate position state that
        # evaluate_signal reads — they MUST acquire this lock too, otherwise
        # a concurrent evaluate_signal call could observe a half-updated
        # position (e.g. ``in_position=True`` but ``_entry_price=0``).
        self._eval_lock = threading.RLock()

    # ── Position state (single source of truth) ──
    # Code Review 3.4: ``entry_price`` / ``entry_qty`` are read-write
    # proxies to ``_entry_price`` / ``_position_qty``.  All internal
    # code should prefer the underscore-prefixed attrs; the proxies
    # exist for backward-compat with callers (and CoinSession mirrors
    # the same names on its own attributes — unrelated to these).
    @property
    def entry_price(self) -> float:
        return self._entry_price

    @entry_price.setter
    def entry_price(self, value: float):
        self._entry_price = float(value or 0.0)

    @property
    def entry_qty(self) -> float:
        return self._position_qty

    @entry_qty.setter
    def entry_qty(self, value: float):
        self._position_qty = float(value or 0.0)

    def set_params(self, amount: float, mode: str, pair: str):
        self.investment_amount = amount
        self.investment_mode = mode
        self.pair = pair

    def update_balance(self, balance: float):
        self.wallet_balance = balance
        if self.investment_mode == "CUMULATIVE":
            self.accumulated_profit = max(0, balance - self.investment_amount)

    def _resolve_ccxt(self):
        """Lazy-bind the live/demo ccxt client from ExchangeManager."""
        if self.ccxt is None and self.exch_mgr is not None:
            self.ccxt = self.exch_mgr.get_current_exchange()
        return self.ccxt

    def evaluate_fli_signal(
        self, fli_buy: bool, fli_sell: bool, price: float, ts
    ):
        """Evaluate FLI indicator signals with 1-candle confirmation.

        Same pending/confirmation flow as evaluate_signal but uses
        the FLI buy_signal / sell_signal columns instead of RSI/MACD.
        """
        with self._eval_lock:
            return self._evaluate_fli_signal_locked(fli_buy, fli_sell, price, ts)

    def _evaluate_fli_signal_locked(
        self, fli_buy: bool, fli_sell: bool, price: float, ts
    ):
        """Inner (locked) FLI signal evaluation."""
        signal = None
        trigger = "fli"

        if fli_buy and not self.in_position:
            signal = "buy_signal"
        elif fli_sell and self.in_position:
            signal = "sell_signal"

        # ── 1-candle confirmation (same pattern as RSI/MACD) ──
        if self.pending_signal is None and signal:
            self.pending_signal = signal
            self.pending_trigger = trigger
            self.confirm_candle_ts = ts
            return {
                "action": "pending",
                "signal": signal,
                "trigger": trigger,
                "note": f"FLI {signal} — awaiting 1-candle confirmation",
                "ts": ts,
                "price": price,
            }

        elif self.pending_signal and self.confirm_candle_ts != ts:
            pending = self.pending_signal
            confirmed = False

            if pending == "buy_signal" and fli_buy and not self.in_position:
                confirmed = True
            elif pending == "sell_signal" and fli_sell and self.in_position:
                confirmed = True

            # Clear pending state BEFORE executing
            self.pending_signal = None
            self.pending_trigger = None
            self.confirm_candle_ts = None

            if confirmed:
                return self._execute_order(pending, price, ts)
            return {
                "action": "rejected",
                "signal": pending,
                "trigger": "fli",
                "note": f"FLI {pending} not confirmed on next candle — rejected",
                "ts": ts,
                "price": price,
            }

        # Same candle as pending — keep waiting
        return None

    def evaluate_signal(
        self, indicators: dict, current_candle: list, all_candles: list
    ):
        """
        Evaluate indicator signals. Requires 1-candle confirmation:
        if signal fires on candle N, wait for candle N+1 to confirm
        before executing.

        Thread-safe: serializes calls via _eval_lock so the parallel
        pipeline thread and the main-thread refresh callback can't
        corrupt pending_signal state.
        """
        with self._eval_lock:
            return self._evaluate_signal_locked(indicators, current_candle, all_candles)

    def _evaluate_signal_locked(
        self, indicators: dict, current_candle: list, all_candles: list
    ):
        """Inner (un-locked) implementation — caller must hold _eval_lock."""
        rsi_vals = indicators.get("rsi_14", [])
        macd_data = indicators.get("macd", {})
        macd_line = macd_data.get("macd_line", [])
        signal_line = macd_data.get("signal_line", [])
        closes = [c[4] for c in all_candles]
        if len(closes) < 2:
            return None

        current_ts = current_candle[0] if current_candle else all_candles[-1][0]
        # Evaluate the candle being inspected (not always the last bar).
        idx = len(closes) - 1
        if current_candle is not None:
            for i, candle in enumerate(all_candles):
                if candle and candle[0] == current_ts:
                    idx = i
                    break
        if idx < 1:
            return None

        # ── Generate signal from the evaluated candle ──
        # Code Review 3.2: track WHICH trigger fired (RSI vs MACD) so the
        # 1-candle confirmation step can re-check the SAME condition rather
        # than always falling back to RSI thresholds.  Previously a
        # MACD-crossover-triggered pending signal was confirmed/rejected
        # against an unrelated RSI threshold.
        signal = None
        trigger = None  # "rsi" | "macd" | None
        rsi_val = rsi_vals[idx] if idx < len(rsi_vals) else None
        if rsi_val is not None and rsi_val < RSI_BUY_THRESHOLD:
            signal = "buy_signal"
            trigger = "rsi"
        elif rsi_val is not None and rsi_val > RSI_SELL_THRESHOLD:
            signal = "sell_signal"
            trigger = "rsi"

        # MACD crossover check (0.0 is a valid MACD value — do not use truthiness)
        ml_now = macd_line[idx] if idx < len(macd_line) else None
        sl_now = signal_line[idx] if idx < len(signal_line) else None
        ml_prev = macd_line[idx - 1] if idx - 1 < len(macd_line) else None
        sl_prev = signal_line[idx - 1] if idx - 1 < len(signal_line) else None
        if (
            ml_now is not None
            and sl_now is not None
            and ml_prev is not None
            and sl_prev is not None
        ):
            if ml_prev <= sl_prev and ml_now > sl_now:
                signal = "buy_signal"
                trigger = "macd"
            elif ml_prev >= sl_prev and ml_now < sl_now:
                signal = "sell_signal"
                trigger = "macd"

        # ── Issue 1 (never set PENDING after BUY / between BUY and SELL):
        #    while the engine holds an open position, BUY signals are
        #    suppressed (the bot is long — it should only look for SELL
        #    signals to exit).  While flat, SELL signals are suppressed
        #    (nothing to sell).  This prevents the engine from ever
        #    firing a BUY-pending while already long, which would
        #    otherwise create a stray PENDING marker between BUY and SELL.
        if signal == "buy_signal" and self.in_position:
            signal = None
            trigger = None
        elif signal == "sell_signal" and not self.in_position:
            signal = None
            trigger = None

        # ── 1-candle confirmation logic (Issue 2 hardening) ──
        # Hard requirement: after a signal is marked PENDING on candle N,
        # the engine MUST wait for the NEXT candle (a candle whose ts
        # differs from confirm_candle_ts) before taking any action.  On
        # the next candle, the SAME indicator condition that originally
        # fired is re-checked; if still valid → execute buy/sell, else
        # → reject and clear the pending state.
        #
        # "Same candle, still waiting" returns None so the chart marker
        # logic doesn't duplicate the PENDING badge on every refresh.
        if self.pending_signal is None and signal:
            # First time we see signal → mark as pending
            self.pending_signal = signal
            self.pending_trigger = trigger  # remember which condition fired
            self.confirm_candle_ts = current_ts
            return {
                "action": "pending",
                "signal": signal,
                "trigger": trigger,
                "note": f"Signal {signal} ({trigger}) detected — awaiting 1-candle confirmation",
                "ts": current_ts,
                "price": closes[idx],
            }

        elif self.pending_signal and self.confirm_candle_ts != current_ts:
            # New candle arrived and signal was pending → confirm if the
            # SAME condition that originally fired is still valid on THIS
            # new candle.  Re-reading the indicator on the new candle is
            # what "wait for one candle after Pending and take an action
            # based on the SAI/FLI indicator" means in practice.
            confirmed_signal = None
            pending = self.pending_signal
            pending_trigger = getattr(self, "pending_trigger", None) or "rsi"

            # Re-check conditions on this new candle
            rsi_now = rsi_vals[idx] if idx < len(rsi_vals) else None
            ml_now_c = macd_line[idx] if idx < len(macd_line) else None
            sl_now_c = signal_line[idx] if idx < len(signal_line) else None
            ml_prev_c = macd_line[idx - 1] if idx - 1 < len(macd_line) else None
            sl_prev_c = signal_line[idx - 1] if idx - 1 < len(signal_line) else None

            if pending == "buy_signal":
                if pending_trigger == "rsi":
                    # RSI buy: original fire was RSI < 30; confirm if
                    # RSI is still in buy territory (relaxed to < 35).
                    if rsi_now is not None and rsi_now < RSI_BUY_CONFIRM:
                        confirmed_signal = "buy_signal"
                else:
                    # MACD buy: original fire was macd line crossing ABOVE
                    # signal line; confirm if macd is STILL above signal
                    # (the crossover hasn't reversed).
                    if (
                        ml_now_c is not None
                        and sl_now_c is not None
                        and ml_now_c > sl_now_c + MACD_BUY_CONFIRM_EPS
                    ):
                        confirmed_signal = "buy_signal"
            elif pending == "sell_signal":
                if pending_trigger == "rsi":
                    if rsi_now is not None and rsi_now > RSI_SELL_CONFIRM:
                        confirmed_signal = "sell_signal"
                else:
                    if (
                        ml_now_c is not None
                        and sl_now_c is not None
                        and ml_now_c < sl_now_c - MACD_SELL_CONFIRM_EPS
                    ):
                        confirmed_signal = "sell_signal"

            # Clear pending state BEFORE executing the order so a slow
            # _execute_order call can't race with a concurrent
            # evaluate_signal that would re-confirm against stale state.
            self.pending_signal = None
            self.pending_trigger = None
            self.confirm_candle_ts = None

            if confirmed_signal:
                return self._execute_order(confirmed_signal, closes[idx], current_ts)
            return {
                "action": "rejected",
                "signal": pending,
                "trigger": pending_trigger,
                "note": f"Signal {pending} ({pending_trigger}) not confirmed on next candle — rejected",
                "ts": current_ts,
                "price": closes[idx],
            }

        # Same candle as the one that fired the pending signal — keep
        # waiting.  Returning None means _on_data_fetched won't re-add
        # a PENDING marker (no duplicate badge) and won't try to confirm.
        return None

    def _execute_order(self, side: str, price: float, ts):
        """Execute confirmed buy or sell order.

        Enforces the user's hard requirements:
          • trading_enabled must be True (Start Trading button armed).
          • engine must not be halted.
          • BUY: only if user does NOT already hold the base coin.
          • BUY: wallet must have enough USDT to cover the investment amount.
          • SELL: only if sell_price > buy_price (else HOLD and log a message).
          • SELL: wallet must have enough base coin to cover the sell qty.
        """
        # ── Master gates ──
        if not self.trading_enabled:
            return {
                "action": "skipped",
                "signal": side,
                "note": "Trading not armed — press 'Start Trading' to enable.",
                "ts": ts,
            }
        if self._halted:
            return {
                "action": "skipped",
                "signal": side,
                "note": "Engine halted — no new orders.",
                "ts": ts,
            }

        amount_usdt = self._get_investment_amount()
        order_id = f"sim-{int(ts)}"
        if self._resolve_ccxt() is None:
            return None

        if side == "buy_signal" and not self.in_position:
            # ── Requirement 2: skip if user already holds the base coin ──
            has_coin, _free_qty = self._has_base_coin()
            if has_coin:
                return {
                    "action": "skipped",
                    "signal": "buy_signal",
                    "note": (
                        f"Hold {self.pair.split('/')[0]} already in wallet — "
                        "waiting for sell signal instead of buying more."
                    ),
                    "ts": ts,
                }

            # ── Requirement 6: check funds before buy ──
            try:
                live_usdt = float(self.exch_mgr.fetch_wallet_coin("USDT"))
            except Exception:
                live_usdt = self.wallet_balance
            if live_usdt < amount_usdt:
                return {
                    "action": "rejected",
                    "signal": "buy_signal",
                    "note": (
                        f"Insufficient USDT: have {live_usdt:.4f}, "
                        f"need {amount_usdt:.4f}"
                    ),
                    "ts": ts,
                }

            qty = amount_usdt / price if price > 0 else 0
            # Place exchange order first; only mutate wallet/position on success.
            try:
                value_usdt = self.on_buy_signal(self.pair, price, Qty=qty)
            except TradeSignal as e:
                return {
                    "action": "rejected",
                    "signal": "buy_signal",
                    "note": f"Buy rejected by exchange: {e}",
                    "ts": ts,
                }
            fill_price = getattr(self, "_entry_price", price) or price
            fill_qty = getattr(self, "_position_qty", qty) or qty
            # Code Review 3.4: entry_price/entry_qty are now property
            # proxies to _entry_price/_position_qty — setting them keeps
            # the canonical state in sync automatically.  No need to
            # assign the underscore-prefixed attrs separately.
            self.entry_price = fill_price
            self.entry_qty = fill_qty
            spent = fill_price * fill_qty
            self.wallet_balance -= spent
            if value_usdt:
                print(f"Buy with Value USDT {value_usdt}")

            trade = {
                "timestamp": datetime.fromtimestamp(
                    ts / 1000 if ts > 1e10 else ts, tz=timezone.utc
                ).isoformat(),
                "side": "buy",
                "symbol": self.pair,
                "price": fill_price,
                "quantity": fill_qty,
                "value_usdt": spent,
                "order_id": order_id,
                "pnl_usdt": None,
                "pnl_pct": None,
                "note": f"{self.investment_mode} buy_signal | notional_target={amount_usdt:.4f} USDT",
            }
            self.logger.log_trade(trade)
            return {
                "action": "buy",
                "price": fill_price,
                "qty": fill_qty,
                "ts": ts,
                "trade": trade,
            }

        elif side == "sell_signal" and self.in_position:
            # Capture position before on_sell_signal clears it.
            entry_qty = self.entry_qty
            entry_price = self.entry_price

            # ── Requirement 3: only sell if sell_price > buy_price, else HOLD ──
            if price <= entry_price:
                return {
                    "action": "hold",
                    "signal": "sell_signal",
                    "note": (
                        f"HOLD: current price {price:.4f} ≤ entry "
                        f"{entry_price:.4f} — waiting for profitable exit."
                    ),
                    "ts": ts,
                    "price": price,
                    "entry_price": entry_price,
                }

            # ── Requirement 6: check funds before sell ──
            try:
                base = self.pair.split("/")[0]
                live_base_qty = float(self.exch_mgr.fetch_wallet_coin(base))
            except Exception:
                live_base_qty = entry_qty
            if live_base_qty < entry_qty:
                return {
                    "action": "rejected",
                    "signal": "sell_signal",
                    "note": (
                        f"Insufficient {self.pair.split('/')[0]}: have "
                        f"{live_base_qty:.8f}, need {entry_qty:.8f}"
                    ),
                    "ts": ts,
                }

            # Pass market price (not notional) as current_price.
            try:
                fill_price = self.on_sell_signal(self.pair, price, Qty=entry_qty)
            except TradeSignal as e:
                return {
                    "action": "rejected",
                    "signal": "sell_signal",
                    "note": f"Sell rejected by exchange: {e}",
                    "ts": ts,
                }
            if fill_price is None:
                return None
            # Code Review 3.3: use the ACTUAL filled qty returned by the
            # exchange (clamped to entry_qty as a safety net in case
            # on_sell_signal didn't get to set _last_fill_qty).
            fill_qty = getattr(self, "_last_fill_qty", None) or entry_qty
            # The exchange may legitimately fill less than entry_qty
            # (partial fill, precision rounding, or our own
            # insufficient-balance fallback).  Use the smaller of the
            # two so PnL/wallet math never overstates the proceeds.
            actual_sell_qty = min(fill_qty, entry_qty) if fill_qty > 0 else entry_qty
            print(
                f"The Sell Action With amount {fill_price} "
                f"(requested {entry_qty:.8f}, filled {actual_sell_qty:.8f})"
            )

            realized_exit = actual_sell_qty * float(fill_price)
            realized_pnl = realized_exit - (actual_sell_qty * entry_price)
            realized_pnl_pct = (
                (realized_pnl / (actual_sell_qty * entry_price)) * 100
                if entry_price
                else 0
            )
            self.wallet_balance += realized_exit
            if self.investment_mode == "CUMULATIVE":
                self.accumulated_profit += realized_pnl

            trade = {
                "timestamp": datetime.fromtimestamp(
                    ts / 1000 if ts > 1e10 else ts, tz=timezone.utc
                ).isoformat(),
                "side": "sell",
                "symbol": self.pair,
                "price": float(fill_price),
                "quantity": actual_sell_qty,
                "value_usdt": realized_exit,
                "order_id": order_id,
                "pnl_usdt": realized_pnl,
                "pnl_pct": realized_pnl_pct,
                "total_pnl": self.accumulated_pnl,
                "note": (
                    f"{self.investment_mode} sell_signal | "
                    f"entry={entry_price:.4f} exit={float(fill_price):.4f} "
                    f"qty={actual_sell_qty:.8f}"
                ),
            }
            self.logger.log_trade(trade)
            # on_sell_signal already called reset_position()
            return {
                "action": "sell",
                "price": float(fill_price),
                "qty": actual_sell_qty,
                "ts": ts,
                "trade": trade,
            }

        return None

    def _get_investment_amount(self):
        if self.investment_mode == "FIXED":
            return self.investment_amount
        else:  # CUMULATIVE
            return self.investment_amount + self.accumulated_profit

    # ─────────────────────────────────────────────────────────────
    # Order placement
    # ─────────────────────────────────────────────────────────────

    def on_buy_signal(
        self, symbol: str, current_price: float | None = None, Qty: float | None = None
    ) -> float | None:
        """
        Place a market BUY if not already long.
        Returns notional value in USDT if an order was placed, else None.
        """
        if self.in_position:
            return None  # Already long — skip duplicate buys
        if current_price is None:
            raise TradeSignal("No market price available for buy")
        if Qty is None:
            raise TradeSignal("No quantity available for buy")

        try:
            print(f"[BUY] Raw qty: {Qty:.8f}")
            if self.exch_mgr is None or self._resolve_ccxt() is None:
                raise TradeSignal("LIVE mode selected but no API credentials provided")
            qty = float(self.ccxt.amount_to_precision(symbol, Qty))
            if qty <= 0:
                raise TradeSignal(
                    f"Computed qty {Qty} rounded to 0 by exchange precision"
                )
            order = self.ccxt.create_market_buy_order(symbol, qty)
            fill_price = float(
                order.get("average") or order.get("price") or current_price
            )
            fill_qty = float(order.get("filled") or qty)
        except TradeSignal:
            raise
        except Exception as e:
            raise TradeSignal(f"Buy order failed: {e}") from e

        # Update state — Code Review High #6: hold _eval_lock during the
        # state mutation so a concurrent evaluate_signal() can't read
        # in_position=True with the old _entry_price (race window that
        # could trigger a phantom SELL before the BUY fill is recorded).
        with self._eval_lock:
            self.in_position = True
            self._entry_price = fill_price
            self._position_qty = fill_qty
            self._last_price = fill_price
            # Code Review 3.3: stash actual fill so _execute_order can use it.
            self._last_fill_price = fill_price
            self._last_fill_qty = fill_qty

        value_usdt = fill_price * fill_qty
        return value_usdt

    def on_sell_signal(
        self, symbol: str, current_price: float | None = None, Qty: float | None = None
    ) -> float | None:
        """
        Liquidate the open long with a market SELL.

        Returns fill price if an order was placed, else None.

        Code Review 3.3: the actual filled quantity is stored on
        ``self._last_fill_qty`` so the caller (_execute_order) can use it
        for PnL/wallet accounting instead of the requested ``entry_qty``
        — which may differ from the fill if the exchange rounds the
        amount via ``amount_to_precision`` or if our insufficient-balance
        fallback clamps ``sell_qty`` to ``sell_current_qty``.
        """
        if not self.in_position:
            return None  # Nothing to sell

        sell_qty = Qty if Qty is not None else self._position_qty or self.entry_qty
        if self.exch_mgr is None or self._resolve_ccxt() is None:
            raise TradeSignal("LIVE mode selected but no API credentials provided")

        sell_current_qty = self.exch_mgr.fetch_wallet_coin(coin=symbol.split("/")[0])
        if to_float(sell_current_qty, 0) < to_float(sell_qty, 0):
            print(
                f"[TRADE] Insufficient balance: have {sell_current_qty}, need {sell_qty}"
            )
            sell_qty = sell_current_qty

        try:
            print(f"[SELL] Raw qty: {to_float(sell_qty, 0):.8f}")
            qty = float(self.ccxt.amount_to_precision(symbol, sell_qty))
            if qty <= 0:
                raise TradeSignal(
                    f"Sell qty {sell_qty} rounded to 0 by exchange precision"
                )
            order = self.ccxt.create_market_sell_order(symbol, qty)
            fill_price = float(
                order.get("average") or order.get("price") or current_price
            )
            fill_qty = float(order.get("filled") or qty)

        except TradeSignal:
            raise
        except Exception as e:
            raise TradeSignal(f"Sell order failed: {e}") from e

        # ── Stash the actual fill qty so _execute_order can use it for
        #    accurate realized-PnL / wallet-balance accounting. ──
        # Code Review High #6: hold _eval_lock for the PnL read + position
        # reset so evaluate_signal can't see a transient state where the
        # SELL has been recorded but in_position is still True (or vice
        # versa).  RLock lets us safely call self.reset_position() inside.
        with self._eval_lock:
            self._last_fill_price = fill_price
            self._last_fill_qty = fill_qty

            # P&L accounting (capture entry price BEFORE resetting position state)
            entry = self._entry_price or self.entry_price
            cost_basis = entry * fill_qty
            proceeds = fill_price * fill_qty
            pnl_usdt = proceeds - cost_basis
            self.accumulated_pnl += pnl_usdt

            # Reset position (RLock — safe to call inside _eval_lock)
            self.reset_position()
            self._last_price = fill_price

        return fill_price

    def reset_position(self):
        """Force-clear position state (e.g., after switching symbol/mode).

        Code Review High #6: acquire ``_eval_lock`` so a concurrent
        ``evaluate_signal`` call on another thread can't observe the
        half-cleared state (``in_position=False`` but ``_entry_price``
        still set to the old fill).
        """
        with self._eval_lock:
            self.in_position = False
            # Code Review 3.4: setting entry_price/entry_qty now propagates
            # to _entry_price/_position_qty via the property setters — no
            # need to assign both pairs.
            self.entry_price = 0.0
            self.entry_qty = 0.0

    # ─────────────────────────────────────────────────────────────
    # Trading gates (Start Trading button + halt/resume)
    # ─────────────────────────────────────────────────────────────

    def set_trading_enabled(self, enabled: bool):
        """Master switch — when False, no buy/sell orders are placed.
        Toggled by the global 'Start Trading' button."""
        self.trading_enabled = bool(enabled)

    def halt(self):
        """Pause trading — no new orders will be placed until resume()."""
        self._halted = True

    def resume(self):
        """Resume trading after halt()."""
        self._halted = False

    def seed_wallet_position(self, entry_price: float, qty: float):
        """Seed a recovered wallet position (from exchange history or current
        holding) into the engine state so the bot knows it already holds the
        base coin and will not buy more.

        Code Review High #6: acquire ``_eval_lock`` so a concurrent
        ``evaluate_signal`` call can't observe the half-seeded state
        (``in_position=True`` but ``_entry_price`` not yet assigned).
        """
        if entry_price and entry_price > 0 and qty and qty > 0:
            with self._eval_lock:
                self.in_position = True
                # Code Review 3.4: single source of truth — property setters
                # propagate to _entry_price / _position_qty.
                self.entry_price = float(entry_price)
                self.entry_qty = float(qty)

    def _has_base_coin(self) -> tuple[bool, float]:
        """Check if the wallet already holds the base coin of self.pair.
        Returns (has_coin, free_qty)."""
        if not self.pair or not self.exch_mgr:
            return (False, 0.0)
        try:
            base = self.pair.split("/")[0]
            free_qty = float(self.exch_mgr.fetch_wallet_coin(base))
            return (free_qty > 0, free_qty)
        except Exception:
            return (False, 0.0)
