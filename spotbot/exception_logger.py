"""Exception logger that writes to a file at project root with [filename:lineno]."""

import os
import sys
import traceback
from datetime import datetime

_LOG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exception_log.txt"
)


def log_exception(e: BaseException, context: str = ""):
    """Log an exception with [filename:lineno] to exception_log.txt at project root."""
    tb = traceback.extract_tb(e.__traceback__)
    if tb:
        frame = tb[-1]
        location = f"[{os.path.relpath(frame.filename, start=os.path.dirname(os.path.dirname(__file__)))}:{frame.lineno}]"
    else:
        location = "[unknown:0]"
    msg = f"{datetime.now().isoformat()} {location} {context}{' ' if context else ''}{type(e).__name__}: {e}"
    with open(_LOG_FILE, "a") as f:
        f.write(msg + "\n")


def excepthook(exc_type, exc_value, exc_tb):
    """Global sys.excepthook that logs unhandled exceptions."""
    tb = traceback.extract_tb(exc_tb)
    if tb:
        frame = tb[-1]
        location = f"[{os.path.relpath(frame.filename, start=os.path.dirname(os.path.dirname(__file__)))}:{frame.lineno}]"
    else:
        location = "[unknown:0]"
    msg = f"{datetime.now().isoformat()} {location} UNHANDLED {exc_type.__name__}: {exc_value}"
    with open(_LOG_FILE, "a") as f:
        f.write(msg + "\n")
    sys.__excepthook__(exc_type, exc_value, exc_tb)
