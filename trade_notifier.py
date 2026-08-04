"""
trade_notifier.py

Cross-platform system notification manager for IndecatorCandel trading bot.

Handles native notifications with platform-specific backends:
    - Windows: native toast notifications via winotify
    - Linux / macOS: system tray notifications via QSystemTrayIcon

Features:
    - Native Windows toast notifications (winotify)
    - Cross-platform fallback via Qt system tray
    - Sound effects for trade events (buy/sell executed, errors)
    - Configurable enable/disable

Adapted from File_Converter_Pro's system_notifier.py for trading use.
"""

import os
import platform
import subprocess
import shutil

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

if IS_WINDOWS:
    try:
        from winotify import Notification as WinotifyNotification

        WINOTIFY_AVAILABLE = True
    except ImportError:
        WINOTIFY_AVAILABLE = False
        print("[NOTIFIER] winotify not installed - falling back to Qt tray")
else:
    WINOTIFY_AVAILABLE = False

try:
    from PySide6.QtWidgets import QSystemTrayIcon, QMenu
    from PySide6.QtGui import QAction, QIcon
except ImportError:
    try:
        from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
        from PyQt6.QtGui import QAction, QIcon
    except ImportError:
        QSystemTrayIcon = None
        QMenu = None
        QAction = None
        QIcon = None


try:
    from playsound3 import playsound

    PLAY_SOUND_AVAILABLE = True
except ImportError:
    PLAY_SOUND_AVAILABLE = False
    print("[NOTIFIER] playsound3 not installed - sounds disabled")


def _resource_path(relative_path: str) -> str:
    """Return absolute path, compatible with dev and PyInstaller."""
    base = getattr(
        __import__("sys"), "_MEIPASS", os.path.dirname(os.path.abspath(__file__))
    )
    return os.path.join(base, relative_path)


class TradeNotifier:
    """
    Cross-platform notification manager for the trading bot.

    Displays system notifications and plays sounds for:
    - BUY trade executed
    - SELL trade executed
    - Trade error
    - Trade skipped (engine declined)
    - General info

    Backend selection (automatic):
    - Windows + winotify installed  -> native Windows toast
    - Otherwise                     -> Qt system tray notification
    """

    APP_NAME = "Trading Bot"
    APP_ID = "IndecatorCandel.TradeNotifier"

    # Sound file mapping (relative to SFX/)
    SOUNDS = {
        "trade_buy": "trade_buy.wav",
        "trade_sell": "trade_sell.wav",
        "signal": "1up.mp3",
        "connect": "connectex.wav",
        "app_start": "welcome.wav",
        "bot_start": "trade_connect.wav",
        "bot_stop": "stopbot.wav",
        "info": "notif.wav",
    }

    def __init__(
        self, enabled: bool = True, tray_icon=None
    ) -> None:
        self.enabled = enabled

        self.icon_path = _resource_path("assets/app.png")
        if not os.path.exists(self.icon_path):
            self.icon_path = ""

        self._sound_paths = {}
        for key, filename in self.SOUNDS.items():
            path = _resource_path(os.path.join("SFX", filename))
            self._sound_paths[key] = path if os.path.exists(path) else None

        self._qt_tray: QSystemTrayIcon | None = tray_icon

        backend = (
            "winotify (Windows)" if (IS_WINDOWS and WINOTIFY_AVAILABLE) else "Qt tray"
        )
        sounds_ok = sum(1 for v in self._sound_paths.values() if v)
        print(
            f"[NOTIFIER] Initialized — backend: {backend} | sounds: {sounds_ok}/{len(self.SOUNDS)}"
        )

    def _ensure_qt_tray(self):
        if QSystemTrayIcon is None:
            return None
        if self._qt_tray is None:
            self._qt_tray = QSystemTrayIcon()
            if self.icon_path and QIcon:
                self._qt_tray.setIcon(QIcon(self.icon_path))
            self._qt_tray.show()
        return self._qt_tray

    def _play_sound(self, event: str) -> None:
        """Play the sound for a given event type with fallback on Linux."""
        if not PLAY_SOUND_AVAILABLE:
            return
        path = self._sound_paths.get(event)
        if not path:
            return
        try:
            playsound(path, block=False)
        except Exception as e:
            print(f"[NOTIFIER] playsound3 error ({event}): {e}")
            self._play_sound_fallback(path, event)

    def _play_sound_fallback(self, path: str, event: str) -> None:
        """Linux fallback: use system audio player via subprocess."""
        if not IS_LINUX:
            return
        for player in ("paplay", "aplay", "mpv", "ffplay", "sox"):
            bin_path = shutil.which(player)
            if not bin_path:
                continue
            try:
                if player in ("aplay",):
                    subprocess.Popen(
                        [bin_path, path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                elif player == "mpv":
                    subprocess.Popen(
                        [bin_path, "--no-video", "--no-terminal", path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                elif player == "ffplay":
                    subprocess.Popen(
                        [bin_path, "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                elif player == "sox":
                    subprocess.Popen(
                        [bin_path, path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.Popen(
                        [bin_path, path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                print(f"[NOTIFIER] Fallback sound ({event}): {player}")
                return
            except Exception:
                continue
        print(f"[NOTIFIER] No audio player found — sound ({event}) skipped")

    def _send_toast(self, title: str, message: str, icon_path: str = "") -> None:
        """Send notification via the best available backend."""
        use_icon = icon_path or self.icon_path

        if IS_WINDOWS and WINOTIFY_AVAILABLE:
            toast = WinotifyNotification(
                app_id=self.APP_ID,
                title=title,
                msg=message,
                duration="short",
                icon=use_icon if use_icon and os.path.exists(use_icon) else "",
            )
            toast.show()
        else:
            tray = self._ensure_qt_tray()
            if tray is not None and QSystemTrayIcon is not None:
                tray.showMessage(
                    title, message, QSystemTrayIcon.MessageIcon.Information, 5000
                )

    # ── Public API ──────────────────────────────────────────────

    def notify_trade(
        self,
        side: str,
        symbol: str,
        price: float,
        qty: float,
        value: float,
        mode: str,
        order_id: str,
        pnl_usdt: float | None = None,
        pnl_pct: float | None = None,
        error: str | None = None,
    ) -> None:
        """
        Send a trade notification.

        Args:
            side:     "buy" or "sell"
            symbol:   Trading pair (e.g. "BTC/USDT")
            price:    Fill price
            qty:      Quantity traded
            value:    Trade value in USDT
            mode:     Trade mode (fixed/cumulative)
            order_id: Exchange order ID
            pnl_usdt: Realized PnL in USDT (for sells, None for buys)
            pnl_pct:  Realized PnL percentage
            error:    Error message if trade failed
        """
        if not self.enabled:
            return

        side_upper = side.upper()

        if error:
            title = f"Trade Error — {side_upper} {symbol}"
            message = f"Error: {error}\nPrice: {price:.4f}"
            self._send_toast(title, message)
            self._play_sound("info")
            return

        pnl_str = ""
        if pnl_usdt is not None:
            pnl_str = f"\nPnL: {pnl_usdt:+.4f} USDT ({pnl_pct:+.2f}%)"

        title = f"{side_upper} Executed — {symbol}"
        message = (
            f"Price: {price:.4f}\n"
            f"Qty: {qty:.6f}\n"
            f"Value: {value:.2f} USDT\n"
            f"Mode: {mode}{pnl_str}"
        )

        self._send_toast(title, message)
        self._play_sound("trade_buy" if side.lower() == "buy" else "trade_sell")

    def notify_error(self, title: str, message: str) -> None:
        """Send a generic error notification with sound."""
        if not self.enabled:
            return
        self._send_toast(f"Error — {title}", message)
        self._play_sound("info")

    def notify_info(self, title: str, message: str) -> None:
        """Send an informational notification with sound."""
        if not self.enabled:
            return
        self._send_toast(title, message)
        self._play_sound("info")

    def notify_skipped(self, reason: str) -> None:
        """Send a trade-skipped notification."""
        if not self.enabled:
            return
        self._send_toast("Trade Skipped", reason)
        self._play_sound("info")

    def notify_signal(self, side: str, symbol: str, price: float) -> None:
        """Play signal sound when a BUY/SELL signal fires but bot is NOT running."""
        if not self.enabled:
            return
        side_upper = side.upper()
        title = f"{side_upper} Signal — {symbol}"
        message = f"Price: {price:.4f}\nBot not running — no trade executed"
        self._send_toast(title, message)
        self._play_sound("signal")

    def notify_connect(self, exchange_name: str, symbol: str) -> None:
        """Play connect sound when exchange connection succeeds."""
        if not self.enabled:
            return
        title = f"Connected — {exchange_name}"
        message = f"Connected to {exchange_name}\nSymbol: {symbol}"
        self._send_toast(title, message)
        self._play_sound("connect")

    def notify_app_start(self) -> None:
        """Play welcome sound when the application opens."""
        if not self.enabled:
            return
        self._play_sound("app_start")

    def notify_bot_start(self, mode: str, symbol: str, timeframe: str) -> None:
        """Play bot-start sound when the trading bot is started."""
        if not self.enabled:
            return
        title = "Bot Started"
        message = f"{mode} · {symbol} · {timeframe}"
        self._send_toast(title, message)
        self._play_sound("bot_start")

    def notify_bot_stop(self, mode: str, message: str = "") -> None:
        """Play bot-stop sound when the trading bot is stopped."""
        if not self.enabled:
            return
        title = "Bot Stopped"
        self._send_toast(title, f"{mode} · {message or 'no open position'}")
        self._play_sound("bot_stop")
