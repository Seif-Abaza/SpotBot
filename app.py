#!/usr/bin/env python3
"""SpotBot (Targov v3.0) — thin entry-point wrapper.

The real implementation now lives in the ``spotbot/`` package.
This file preserves the ``python3 app.py`` invocation for backward
compatibility.

PySide6/PyQt6 compatibility shim: trade_notifier.py imports PyQt6, but
this app uses PySide6.  We alias PyQt6.* -> PySide6.* in sys.modules so
the notifier module imports cleanly without requiring PyQt6 to be
installed.
"""

import sys

# ── Compatibility shim: PyQt6.* -> PySide6.* ──
try:
    import types as _types

    import PySide6

    # PySide6 submodules aren't auto-imported; import them explicitly
    import PySide6.QtCore
    import PySide6.QtGui
    import PySide6.QtWidgets

    if "PyQt6" not in sys.modules:
        _pkg = _types.ModuleType("PyQt6")
        _pkg.__path__ = []
        sys.modules["PyQt6"] = _pkg
        for _sub in ("QtCore", "QtGui", "QtWidgets"):
            _full = f"PyQt6.{_sub}"
            if _full not in sys.modules:
                sys.modules[_full] = sys.modules[f"PySide6.{_sub}"]
    try:
        import trade_notifier  # noqa: F401

        TRADE_NOTIFIER_AVAILABLE = True
    except Exception as _tn_err:
        TRADE_NOTIFIER_AVAILABLE = False
        print(f"[NOTIFIER] trade_notifier unavailable: {_tn_err}")
except Exception as _shim_err:
    TRADE_NOTIFIER_AVAILABLE = False
    print(f"[NOTIFIER] PySide6 alias shim failed: {_shim_err}")

from spotbot.main import main

if __name__ == "__main__":
    main()
