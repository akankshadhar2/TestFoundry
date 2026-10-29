"""Colored banners and status lines. Respects NO_COLOR; degrades to ASCII if the terminal can't encode symbols."""

import os
import sys
import threading

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

_lock = threading.Lock()


def _can_encode(text: str) -> bool:
    try:
        text.encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


UNICODE = _can_encode("✓✗⚡━·…→")
COLOR = (not os.environ.get("NO_COLOR")) and hasattr(sys.stdout, "isatty") and sys.stdout.isatty() \
    and os.environ.get("TERM") != "dumb"

SYM = {
    "ok": "✓" if UNICODE else "OK",
    "warn": "!" if UNICODE else "!!",
    "fail": "✗" if UNICODE else "XX",
    "info": "·" if UNICODE else "-",
    "bolt": "⚡" if UNICODE else ">>",
    "arrow": "→" if UNICODE else "->",
    "dot": "·" if UNICODE else "-",
    "ellipsis": "…" if UNICODE else "...",
    "rule": "━" if UNICODE else "=",
}


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def green(t): return _c("32", t)
def yellow(t): return _c("33", t)
def red(t): return _c("31", t)
def cyan(t): return _c("36", t)
def bold(t): return _c("1", t)
def dim(t): return _c("2", t)


def say(text: str = "") -> None:
    with _lock:
        print(text, flush=True)


def banner(step, title: str) -> None:
    if step is None:
        label = title
    elif isinstance(step, int):
        label = f"STEP {step} {SYM['dot']} {title}"
    else:
        label = f"{step} {SYM['dot']} {title}"
    rule = SYM["rule"] * max(48, len(label) + 4)
    say()
    say(cyan(rule))
    say(bold(f"  {label}"))
    say(cyan(rule))


def ok(text: str) -> None:
    say(f"  {green(SYM['ok'])} {text}")


def warn(text: str) -> None:
    say(f"  {yellow(SYM['warn'])} {yellow(text)}")


def fail(text: str) -> None:
    say(f"  {red(SYM['fail'])} {red(text)}")


def info(text: str) -> None:
    say(f"  {dim(SYM['info'])} {text}")


def detail(text: str) -> None:
    say(dim(f"      {text}"))


def loud(text: str) -> None:
    """A warning the presenter cannot miss."""
    line = "!" * min(72, max(20, len(text) + 4))
    say(yellow(line))
    say(yellow(bold(f"  {text}")))
    say(yellow(line))


def stream_write(text: str) -> None:
    """Live-print streamed model output, dimmed."""
    with _lock:
        sys.stdout.write(dim(text) if COLOR else text)
        sys.stdout.flush()
