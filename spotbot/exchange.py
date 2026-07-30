"""Exchange connection, wallet, candles, and API-key management."""

import asyncio
import base64
import hashlib
import json
import os
import random
import time

import ccxtpro

try:
    import ccxt

    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False

from spotbot.constants import (
    API_KEY_FILE,
    API_KEY_PASSPHRASE,
    CCXT_PRO_AVAILABLE,
    CONFIG_DIR,
    CCXT_AVAILABLE as _CCXT_AVAILABLE,
    FLOAT_EPS,
    QUOTE_ASSETS,
    TIMEFRAME_MAP,
    WALLET_MIN_NOTIONAL_USDT,
    ALLOW_MOCK_CANDLES,
    TRADE_HISTORY_LIMIT,
    CANDLE_LIMIT,
)


def compute_avg_entry_from_trades(trades, current_qty: float):
    """
    Reconstruct average entry for *current_qty* using FIFO lot matching on
    exchange trade history. Returns (avg_entry, recovered_qty) or None.
    """
    try:
        need = float(current_qty)
    except (TypeError, ValueError):
        return None
    if need <= FLOAT_EPS:
        return None
    if not trades:
        return None

    lots = []  # [price, qty]
    ordered = sorted(
        trades,
        key=lambda t: t.get("timestamp") or t.get("datetime") or 0,
    )
    for t in ordered:
        side = str(t.get("side") or "").lower()
        try:
            px = float(t.get("price") or 0)
            amt = float(t.get("amount") or t.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if px <= 0 or amt <= 0:
            continue
        if side == "buy":
            lots.append([px, amt])
        elif side == "sell":
            left = amt
            while left > FLOAT_EPS and lots:
                take = min(lots[0][1], left)
                lots[0][1] -= take
                left -= take
                if lots[0][1] <= FLOAT_EPS:
                    lots.pop(0)

    total = sum(q for _, q in lots)
    if total <= FLOAT_EPS:
        return None

    target = min(need, total)
    cost = 0.0
    got = 0.0
    # ── Code Review 3.1: walk lots OLDEST-first (natural order).
    #    `lots` is already in chronological order (buys appended in
    #    timestamp order, sells consumed from the front via pop(0)).
    #    The previous code walked `reversed(lots)` — newest-first —
    #    which is LIFO cost selection on top of FIFO lot consumption,
    #    a hybrid that diverges from both pure FIFO and pure LIFO.
    #    Pure FIFO means: the oldest remaining open lots ARE the
    #    current position's cost basis.  This also matches the
    #    `sell_qty = sell_current_qty` fallback in `on_sell_signal`
    #    and the way `TradingEngine.entry_price` is interpreted in
    #    the "sell only if price > entry" gate. ──
    for px, qty in lots:
        if got >= target - FLOAT_EPS:
            break
        take = min(qty, target - got)
        cost += px * take
        got += take
    if got <= FLOAT_EPS:
        return None
    return (cost / got, got)


class ExchangeManager:
    """Handles ccxt exchange connection, wallet, candles, pairs, WebSocket."""

    def __init__(self):
        self.exchange = None
        self.ws_exchange = None
        self.exchange_name = ""
        self.is_demo = True
        self._api_keys = self._load_api_keys()

    # ── API Keys (Code Review 2.1: encrypt at rest + chmod 0o600) ──
    #
    # Storage format (api_keys.json):
    #   { "v": 1, "encrypted": true, "data": "<base64-Fernet-token>" }
    # If `cryptography.fernet.Fernet` is unavailable (no cryptography
    # package installed) we fall back to plaintext + chmod 0o600, which
    # is still strictly better than the original plaintext-with-default-
    # perms behaviour.  Old plaintext files are auto-migrated on next
    # save.

    @staticmethod
    def _derive_fernet_key():
        """Derive a Fernet key from the machine-local passphrase.

        Uses SHA-256 of the passphrase → base64-encoded 32-byte key.
        Fernet requires a 32-byte URL-safe base64 key.
        """
        digest = hashlib.sha256(API_KEY_PASSPHRASE).digest()
        return base64.urlsafe_b64encode(digest)

    def _load_api_keys(self):
        if not API_KEY_FILE.exists():
            return {}
        try:
            raw = API_KEY_FILE.read_text()
            # Try encrypted format first
            try:
                envelope = json.loads(raw)
                if (
                    isinstance(envelope, dict)
                    and envelope.get("v") == 1
                    and envelope.get("encrypted")
                ):
                    from cryptography.fernet import Fernet  # type: ignore

                    key = self._derive_fernet_key()
                    token = envelope.get("data", "").encode()
                    plaintext = Fernet(key).decrypt(token).decode("utf-8")
                    return json.loads(plaintext)
            except (ImportError, Exception):
                # Either not encrypted, or cryptography not installed —
                # fall through to plaintext parser below.
                pass
            # Plaintext (legacy or fallback)
            return json.loads(raw)
        except Exception:
            return {}

    def _save_api_keys(self, keys):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            from cryptography.fernet import Fernet  # type: ignore

            key = self._derive_fernet_key()
            plaintext = json.dumps(keys, indent=2).encode("utf-8")
            token = Fernet(key).encrypt(plaintext).decode("ascii")
            payload = {"v": 1, "encrypted": True, "data": token}
            API_KEY_FILE.write_text(json.dumps(payload, indent=2))
        except ImportError:
            # cryptography not installed — fall back to plaintext, but
            # still chmod 0o600 so we're at least no worse than before.
            API_KEY_FILE.write_text(json.dumps(keys, indent=2))
        # Always restrict file permissions (no-op on Windows but harmless).
        try:
            os.chmod(API_KEY_FILE, 0o600)
        except OSError as e:
            # Code Review Medium #7: log instead of silent pass.  chmod is
            # best-effort — failure here doesn't compromise the encrypted
            # payload, only the file-system permission bits.
            print(f"[_save_api_keys] os.chmod failed: {e}")

    def get_api_key(self, exchange_name):
        return self._api_keys.get(exchange_name.lower(), {})

    def set_api_key(self, exchange_name, api_key, secret, mode="live"):
        k = self._api_keys.get(exchange_name.lower(), {})
        k[mode] = {"apiKey": api_key, "secret": secret}
        self._api_keys[exchange_name.lower()] = k
        self._save_api_keys(self._api_keys)

    def get_active_key(self, exchange_name, is_demo):
        k = self._api_keys.get(exchange_name.lower(), {})
        mode = "demo" if is_demo else "live"
        return k.get(mode, k.get("live", {}))

    # ── Connection ──

    def connect(self, exchange_name, is_demo=True):
        self.exchange_name = exchange_name
        self.is_demo = is_demo
        if not CCXT_AVAILABLE:
            return True
        try:
            cls = getattr(ccxt, exchange_name.lower(), None)
            if cls is None:
                return False
            keys = self.get_active_key(exchange_name, is_demo)
            config = {
                "apiKey": keys.get("apiKey", ""),
                "secret": keys.get("secret", ""),
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
            self.exchange = cls(config)
            self.exchange.enable_demo_trading(is_demo)
            self.exchange.load_markets()
            return True
        except Exception as e:
            print(f"Error {e}")
            self.exchange = None
            return False

    def disconnect(self):
        if self.exchange:
            try:
                if hasattr(self.exchange, 'close'):
                    self.exchange.close()
            except Exception as e:
                print(f"[disconnect] exchange.close failed: {e}")
            self.exchange = None
        if self.ws_exchange:
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.ws_exchange.close())
                loop.close()
            except Exception as e:
                print(f"[disconnect] ws_exchange.close failed: {e}")
            self.ws_exchange = None

    def fetch_other_coin(self, balance_in_wallet):
        coin_free_wallet = []
        for current_coin in balance_in_wallet:
            coin_symbol = "".join(
                ch for ch in str(current_coin) if ch.isalpha() and ch.isupper()
            )
            if coin_symbol:
                if coin_symbol in [
                    "USDT",
                    "USDC",
                    "USD1",
                    "PYUSD",
                    "TUSD",
                    "USAT",
                    "USDE",
                    "USDTB",
                ]:
                    continue
                coin_info = balance_in_wallet.get(coin_symbol, {})
                coin_free = coin_info.get("free", 0.0) or 0.0
                coin_free_wallet.append([coin_symbol, coin_free])
        return coin_free_wallet

    def fetch_wallet_coin(self, coin="USDT"):
        if not CCXT_AVAILABLE or not self.exchange:
            return 100.0
        try:
            bal = self.exchange.fetch_balance()

            info = bal.get(coin, {})
            free = info.get("free", 0.0) or 0.0
            return float(free)
        except Exception as e:
            print(f"fetch_wallet_coin error: {e}")
            return 0.0

    def fetch_all_balances(self):
        """Return {coin: {free, used, total}} for non-zero balances."""
        if not CCXT_AVAILABLE or not self.exchange:
            return {
                "USDT": {"free": 1000.0, "used": 0.0, "total": 1000.0},
                "BTC": {"free": 0.01, "used": 0.0, "total": 0.01},
                "ETH": {"free": 0.2, "used": 0.0, "total": 0.2},
            }
        try:
            bal = self.exchange.fetch_balance() or {}
            out = {}
            totals = bal.get("total") or {}
            frees = bal.get("free") or {}
            useds = bal.get("used") or {}
            coins = set(totals) | set(frees) | set(useds)
            for coin in coins:
                if not coin or str(coin).startswith("info"):
                    continue
                free = float(frees.get(coin) or 0)
                used = float(useds.get(coin) or 0)
                total = float(totals.get(coin) or (free + used))
                if total > FLOAT_EPS:
                    out[str(coin).upper()] = {
                        "free": free,
                        "used": used,
                        "total": total,
                    }
            return out
        except Exception as e:
            print(f"fetch_all_balances error: {e}")
            return {}

    def discover_tradable_pairs(
        self, quote="USDT", min_notional_usdt=WALLET_MIN_NOTIONAL_USDT
    ):
        """
        Find non-dust base coins in the wallet that have a COIN/quote spot market.
        Returns list of dicts: {coin, qty, pair, notional_est}.

        Issue 3 fixes:
          • Ensures markets are loaded (load_markets()) before searching —
            previously self.exchange.markets could be None on a fresh
            connection, causing every pair to be skipped.
          • Uses fetch_tickers() (batch) instead of fetch_ticker() per
            coin — one HTTP round-trip instead of N.  Falls back to
            per-coin fetch_ticker only if the exchange doesn't support
            batch tickers.
          • Robustly handles ccxt's market dict (which can use either
            "BASE/QUOTE" string keys or market objects with .symbol).
          • Treats dust balances (< min_notional_usdt) as "not held" so
            the bot doesn't open tabs for stale dust.
        """
        if not CCXT_AVAILABLE or self.exchange is None:
            # Mock path — return the same mock balances fetch_all_balances does
            balances = self.fetch_all_balances()
            found = []
            mock_px = {
                "BTC/USDT": 42000,
                "ETH/USDT": 2200,
                "SOL/USDT": 150,
            }
            for coin, info in balances.items():
                if coin in QUOTE_ASSETS:
                    continue
                qty = float(info.get("total") or 0)
                if qty <= FLOAT_EPS:
                    continue
                pair = f"{coin}/{quote}"
                notional = mock_px.get(pair, 100.0) * qty
                if notional < float(min_notional_usdt):
                    continue
                found.append(
                    {"coin": coin, "qty": qty, "pair": pair, "notional_est": notional}
                )
            return found

        # ── Real exchange path ──
        balances = self.fetch_all_balances()
        if not balances:
            print("[discover_tradable_pairs] no non-zero balances returned")
            return []

        # Make sure markets are loaded — without this, the markets dict
        # is empty on a fresh connection and EVERY pair gets filtered out.
        markets = {}
        try:
            if not getattr(self.exchange, "markets", None):
                self.exchange.load_markets()
            markets = self.exchange.markets or {}
        except Exception as e:
            print(f"[discover_tradable_pairs] load_markets failed: {e}")
            markets = self.exchange.markets or {}

        # Build the set of valid spot pairs keyed by uppercase symbol.
        valid_symbols = set()
        if markets:
            for sym, mkt in markets.items():
                try:
                    is_spot = (
                        mkt.get("spot", False) if isinstance(mkt, dict) else False
                    ) or (
                        (mkt.get("type", "") if isinstance(mkt, dict) else "") == "spot"
                    )
                    if not is_spot and mkt is not None:
                        # Some exchanges don't tag spot explicitly; accept
                        # any market whose symbol contains "/".
                        is_spot = "/" in str(sym)
                    if is_spot:
                        valid_symbols.add(str(sym).upper())
                except Exception:
                    continue

        # Batch-fetch tickers (1 HTTP call) for price discovery.
        tickers = {}
        try:
            tickers = self.exchange.fetch_tickers() or {}
        except Exception as e:
            print(f"[discover_tradable_pairs] fetch_tickers failed: {e}")
            tickers = {}

        found = []
        for coin, info in balances.items():
            if coin in QUOTE_ASSETS:
                continue
            # Use TOTAL balance (free + used) — previously used "total"
            # but the key was sometimes missing; fall back to free+used.
            free = float(info.get("free") or 0)
            used = float(info.get("used") or 0)
            total = float(info.get("total") or (free + used))
            if total <= FLOAT_EPS:
                continue
            pair = f"{coin}/{quote}"
            # Validate the pair exists on the exchange's spot markets.
            if valid_symbols and pair.upper() not in valid_symbols:
                # Case-insensitive fallback — some exchanges use mixed-case
                # symbols (e.g. "BTC/USDT" vs "btcusdt").  Match against
                # the original-case exchange symbol so we can use it for
                # subsequent fetch_ticker / fetch_ohlcv calls.
                alt = next(
                    (
                        orig
                        for orig in self.exchange.markets.keys()
                        if orig.upper() == pair.upper()
                    ),
                    None,
                )
                if alt is None:
                    continue
                pair = alt
            # Estimate notional via batch ticker (preferred) → per-pair
            # ticker (fallback) → OHLCV last close (last resort).
            notional = 0.0
            ticker = tickers.get(pair) or tickers.get(pair.upper())
            if ticker:
                try:
                    last = float(
                        ticker.get("last")
                        or ticker.get("close")
                        or ticker.get("bid")
                        or 0
                    )
                    notional = last * total
                except (TypeError, ValueError, AttributeError):
                    notional = 0.0
            if notional <= 0.0:
                # Fallback: per-pair ticker (slower, one HTTP call).
                try:
                    t = self.exchange.fetch_ticker(pair)
                    last = float(t.get("last") or t.get("close") or t.get("bid") or 0)
                    notional = last * total
                except Exception:
                    pass
            if notional <= 0.0:
                # Last resort: pull the latest OHLCV close.  This is
                # accurate but costs one HTTP call per coin — only used
                # when the exchange doesn't support fetch_tickers.
                try:
                    ohlcv = self.exchange.fetch_ohlcv(pair, "1m", limit=1)
                    if ohlcv:
                        notional = float(ohlcv[-1][4]) * total
                except Exception:
                    pass
            if notional <= 0.0:
                # Truly no price info — skip rather than guess.
                continue
            if notional < float(min_notional_usdt):
                continue
            found.append(
                {
                    "coin": coin,
                    "qty": total,
                    "pair": pair,
                    "notional_est": notional,
                }
            )
        return found

    def fetch_my_trades(self, pair, limit=TRADE_HISTORY_LIMIT):
        """Fetch recent user trades for a pair (empty list on failure/mock)."""
        if not CCXT_AVAILABLE or not self.exchange:
            return []
        try:
            return self.exchange.fetch_my_trades(pair, limit=limit) or []
        except Exception as e:
            print(f"fetch_my_trades error ({pair}): {e}")
            return []

    def fetch_avg_entry_price(self, pair, current_qty):
        """Return (avg_entry, recovered_qty) from exchange history, or None."""
        trades = self.fetch_my_trades(pair)
        return compute_avg_entry_from_trades(trades, current_qty)

    # ── fetch_ohlcv ──

    def _normalize_ohlcv_timestamp(self, ts):
        """Normalize exchange OHLCV timestamps to milliseconds internally."""
        try:
            value = float(ts)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        if abs(value) <= 1e10:
            return int(value * 1000)
        return int(value)

    def fetch_ohlcv(self, pair, timeframe, limit=CANDLE_LIMIT):
        candles = []
        if not CCXT_AVAILABLE or not self.exchange:
            if not ALLOW_MOCK_CANDLES:
                print(
                    "[fetch_ohlcv] No exchange backend and APPV3_ALLOW_MOCKS not set — returning empty candles"
                )
                return []
            candles = self._mock_candles(pair, timeframe, limit)
        else:
            try:
                candles = self.exchange.fetch_ohlcv(pair, timeframe, limit=limit) or []
            except Exception as e:
                print(f"[fetch_ohlcv] exchange.fetch_ohlcv failed: {e}")
                candles = []

        normalized = []
        for c in candles:
            if not isinstance(c, (list, tuple)) or len(c) < 5:
                continue
            ts = self._normalize_ohlcv_timestamp(c[0])
            if ts is None:
                continue
            normalized.append(
                [
                    ts,
                    float(c[1]),
                    float(c[2]),
                    float(c[3]),
                    float(c[4]),
                    float(c[5]) if len(c) > 5 else 0.0,
                ]
            )
        return normalized

    # ── get_all_pairs (spot only) ──

    def get_all_pairs(self, exchange_name, progress_callback=None):
        """Return list of spot pairs for the given exchange."""
        if not CCXT_AVAILABLE:
            return ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
        try:
            cls = getattr(ccxt, exchange_name.lower(), None)
            if cls is None:
                return []
            config = {
                "enableRateLimit": True,
            }
            ex = cls(config)
            ex.load_markets()
            spot_pairs = []
            total = len(ex.markets)
            for i, (sym, mkt) in enumerate(ex.markets.items()):
                if mkt.get("spot", False) or mkt.get("type", "") == "spot":
                    if "/USDT" in sym or "/USD" in sym:
                        spot_pairs.append(sym)
                if progress_callback and (i + 1) % 50 == 0:
                    progress_callback(int((i + 1) / total * 100))
            if progress_callback:
                progress_callback(100)
            return sorted(spot_pairs)
        except Exception:
            return []

    def create_ws_exchange(self, exchange_name, is_demo=True):
        """Create ccxt.pro WebSocket exchange for real-time data."""
        if not CCXT_PRO_AVAILABLE:
            return None
        try:
            cls = getattr(ccxtpro, exchange_name.lower(), None)
            if cls is None:
                return None
            keys = self.get_active_key(exchange_name, is_demo)
            config = {
                "apiKey": keys.get("apiKey", ""),
                "secret": keys.get("secret", ""),
                "enableRateLimit": True,
            }
            self.ws_exchange = cls(config)
            self.ws_exchange.enable_demo_trading(is_demo)
            return self.ws_exchange
        except Exception:
            return None

    # ── Mock candles ──

    def _mock_candles(self, pair, timeframe, limit=CANDLE_LIMIT):
        tf_sec = TIMEFRAME_MAP.get(timeframe, 300)
        base_ts = int(time.time()) - limit * tf_sec
        base_price = {"BTC/USDT": 42000, "ETH/USDT": 2200, "SOL/USDT": 150}.get(
            pair, 100.0
        )
        price = base_price
        candles = []
        for i in range(limit):
            ts = base_ts + i * tf_sec
            o = price
            v = price * 0.005
            h = o + random.uniform(0, v)
            l = o - random.uniform(0, v)
            c = o + random.uniform(-v * 0.5, v * 0.5)
            vol = random.uniform(10, 500)
            candles.append([int(ts * 1000), o, h, l, c, vol])
            price = c
        return candles

    def get_current_exchange(self, websocket=False):
        if self.exchange is not None:
            if not websocket:
                return self.exchange
        if self.ws_exchange is not None:
            if websocket:
                return self.ws_exchange
        return None
