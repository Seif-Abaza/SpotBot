"""Per-day trade logging: transactions/ JSON files + daily P&L ledger."""

import json
import threading
from datetime import datetime, timezone

from spotbot.constants import CONFIG_DIR, PNL_LOG_FILE


class TransactionLogger:
    """Logs trades in the exact PnL JSON format from the specification."""

    def __init__(self):
        self.trades = []
        # Code Review 3.5: use UTC for the daily PnL date window so it
        # matches the UTC timestamps stored on each trade (set in
        # TradingEngine._execute_order via tz=timezone.utc).  Mixing
        # local-time date strings with UTC trade timestamps caused
        # round-trips that closed near local midnight to land on the
        # "wrong" day in the daily PnL summary.
        self.date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.exchange_name = ""
        self.mode = "SIMULATOR"
        self.investment_mode = "FIXED"
        self.investment_amount = 0.0
        # Guards self.trades so multiple CoinPipelineThreads can log concurrently;
        # UI must still only be touched from the main thread via signals.
        self._lock = threading.Lock()

    def set_meta(self, exchange_name, is_demo, investment_mode, investment_amount=0.0):
        self.exchange_name = exchange_name
        self.mode = "SIMULATOR" if is_demo else "LIVE"
        self.investment_mode = investment_mode.upper()
        self.investment_amount = investment_amount

    def log_trade(self, trade: dict):
        with self._lock:
            self.trades.append(trade)

    def get_pnl_data(self):
        with self._lock:
            trades = list(self.trades)

        normalized_trades = []
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            normalized_trade = dict(trade)
            normalized_trade.setdefault("side", "unknown")
            normalized_trade.setdefault("pnl_usdt", None)
            normalized_trades.append(normalized_trade)

        buys = sum(
            1 for t in normalized_trades if str(t.get("side", "")).lower() == "buy"
        )
        sells = sum(
            1 for t in normalized_trades if str(t.get("side", "")).lower() == "sell"
        )
        realized = sum(
            float(t.get("pnl_usdt", 0) or 0)
            for t in normalized_trades
            if t.get("pnl_usdt") is not None
        )

        return {
            "date": self.date,
            "exchange": self.exchange_name,
            "mode": self.mode,
            "investment_mode": self.investment_mode,
            "trades": normalized_trades,
            "summary": {
                "total_trades": len(normalized_trades),
                "buys": buys,
                "sells": sells,
                "realized_pnl_usdt": realized,
                "realized_pnl_pct": (
                    realized / self.investment_amount * 100
                    if self.investment_amount
                    else 0
                ),
            },
        }

    def get_round_trips(self, symbol: str | None = None):
        """
        Pair buy -> sell trades per symbol (FIFO) into round-trip rows.
        Each row: symbol, buy_price, sell_price, qty, buy_ts, sell_ts,
        pnl_usdt, pnl_pct. A round-trip only appears once its sell has
        logged; an open (unsold) buy is not included.
        """
        with self._lock:
            trades = list(self.trades)
        open_buys = {}  # symbol -> FIFO list of buy trade dicts
        round_trips = []
        # Code Review Additional #17: track orphaned sells (SELL with no
        # matching BUY in the FIFO queue).  Previously these were silently
        # dropped, which could happen on bot restart mid-position — the
        # wallet already held the base coin (seeded via seed_wallet_position)
        # but the matching BUY was never logged in this session.  Now we
        # surface them as synthetic round-trips with buy_price=None so the
        # PnL dialog can show "unrealized" / "unknown cost basis" instead
        # of pretending the SELL never happened.
        orphaned_sells = []
        for t in sorted(trades, key=lambda x: x.get("timestamp") or ""):
            sym = t.get("symbol", "")
            if symbol is not None and sym != symbol:
                continue
            side = t.get("side")
            if side == "buy":
                open_buys.setdefault(sym, []).append(t)
            elif side == "sell":
                buys = open_buys.get(sym)
                if not buys:
                    # Orphaned SELL — log once per occurrence so the user
                    # can investigate (typically means the BUY happened in
                    # a previous session and wasn't reloaded from history).
                    orphaned_sells.append(t)
                    print(
                        f"[TransactionLogger] orphaned SELL for {sym} at "
                        f"{t.get('timestamp')} — no matching BUY in trade log"
                    )
                    round_trips.append(
                        {
                            "symbol": sym,
                            "buy_price": None,  # unknown cost basis
                            "sell_price": t.get("price"),
                            "qty": t.get("quantity"),
                            "buy_ts": None,
                            "sell_ts": t.get("timestamp"),
                            "pnl_usdt": t.get("pnl_usdt"),
                            "pnl_pct": t.get("pnl_pct"),
                            "orphaned": True,  # flag for UI to highlight
                        }
                    )
                    continue
                buy = buys.pop(0)
                round_trips.append(
                    {
                        "symbol": sym,
                        "buy_price": buy.get("price"),
                        "sell_price": t.get("price"),
                        "qty": t.get("quantity"),
                        "buy_ts": buy.get("timestamp"),
                        "sell_ts": t.get("timestamp"),
                        "pnl_usdt": t.get("pnl_usdt"),
                        "pnl_pct": t.get("pnl_pct"),
                    }
                )
        return round_trips

    def get_daily_pnl(self, date: str | None = None):
        """Realized PnL summed over round-trips whose sell landed on `date`
        (YYYY-MM-DD, defaults to today in UTC).

        Code Review 3.5: trades are timestamped with ``tz=timezone.utc``
        in TradingEngine._execute_order, so the date window must also be
        UTC for the comparison to be consistent across timezones."""
        target_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        total = 0.0
        count = 0
        for rt in self.get_round_trips():
            sell_ts = rt.get("sell_ts") or ""
            if str(sell_ts)[:10] == target_date:
                total += rt.get("pnl_usdt") or 0.0
                count += 1
        return {
            "date": target_date,
            "realized_pnl_usdt": total,
            "round_trips": count,
        }

    def save_to_file(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = self.get_pnl_data()
        PNL_LOG_FILE.write_text(json.dumps(data, indent=2))

    def reset(self):
        with self._lock:
            self.trades = []
        # Code Review 3.5: UTC, see __init__.
        self.date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def reset_all(self):
        """Issue 4: full reset — clears in-memory trades AND the persisted
        PNL_LOG_FILE on disk so the Daily P&L table starts fresh.

        This is destructive and cannot be undone.  Use it to start a new
        accounting session (e.g. after switching LIVE/DEMO, or to clear
        stale test trades that were logged before the bot was correctly
        configured).
        """
        with self._lock:
            self.trades = []
            self.date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Remove the persisted PnL file so opening the dialog after reset
        # doesn't show stale trades from the previous session.
        try:
            if PNL_LOG_FILE.exists():
                PNL_LOG_FILE.unlink()
        except Exception as e:
            print(f"[TransactionLogger.reset_all] failed to remove {PNL_LOG_FILE}: {e}")
