"""Claude calls (streaming + non-streaming) and JSON / code extraction.

Two backends, picked by CLAUDE_BACKEND (auto|api|cli, default auto):
  api  the Anthropic API (needs ANTHROPIC_API_KEY; billed per call)
  cli  the Claude Code command `claude -p` (uses your Claude subscription; no API key)
auto uses the API when ANTHROPIC_API_KEY is set, otherwise Claude Code if it is installed.

Every failure (package missing, no key, API error, network, refusal) becomes ClaudeUnavailable,
so callers have exactly one thing to catch before falling back to the backup set.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

from toolkit import config, ui


class ClaudeUnavailable(Exception):
    pass


_client = None
_client_lock = threading.Lock()
_cli_slots = threading.Semaphore(3)  # parallel fixture calls: at most 3 `claude` processes at once
CLI_TIMEOUT = 600


def _api_problem():
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return "the 'anthropic' package is not installed (pip install -r requirements.txt)"
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "ANTHROPIC_API_KEY is not set (add it to .env)"
    return None


def _cli_problem():
    if not shutil.which("claude"):
        return "the Claude Code command `claude` is not installed (https://claude.com/claude-code)"
    return None


def backend() -> str:
    """'api' or 'cli': which way Claude would be called right now."""
    config.load_env()
    choice = os.environ.get("CLAUDE_BACKEND", "auto").strip().lower()
    if choice in ("api", "cli"):
        return choice
    return "api" if _api_problem() is None or _cli_problem() is not None else "cli"


def problem():
    """Why Claude can't be used right now (without calling it), or None."""
    config.load_env()
    choice = os.environ.get("CLAUDE_BACKEND", "auto").strip().lower()
    if choice == "api":
        return _api_problem()
    if choice == "cli":
        return _cli_problem()
    if _api_problem() is None or _cli_problem() is None:
        return None
    return "ANTHROPIC_API_KEY is not set and Claude Code (`claude`) is not installed"


def describe() -> str:
    """How Claude is being reached, for status lines. Never includes a key."""
    return "Anthropic API" if backend() == "api" else "Claude Code (your subscription, no API key)"


def model() -> str:
    return config.model()


def _get_client():
    global _client
    why = problem()
    if why:
        raise ClaudeUnavailable(why)
    with _client_lock:
        if _client is None:
            import anthropic
            try:
                _client = anthropic.Anthropic(max_retries=4, timeout=180)
            except Exception as e:  # e.g. bad credentials configuration
                raise ClaudeUnavailable(f"could not create Anthropic client: {type(e).__name__}") from None
    return _client


def _params(prompt: str, system, max_tokens: int) -> dict:
    params = {"model": model(), "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]}
    if system:
        params["system"] = system
    effort = os.environ.get("CLAUDE_EFFORT", "").strip()
    if effort:  # optional; only sent when set, so older models never see an unknown field
        params["output_config"] = {"effort": effort}
    return params


def _describe_error(e: Exception) -> str:
    status = getattr(e, "status_code", None)
    body = getattr(e, "body", None)
    err = body.get("error") if isinstance(body, dict) else None
    msg = (err.get("message") if isinstance(err, dict) else None) or getattr(e, "message", None) or str(e)
    msg = msg.splitlines()[0][:200] if msg else ""
    return f"{type(e).__name__}{f' (HTTP {status})' if status else ''}: {msg}"


def _text_of(message) -> str:
    if getattr(message, "stop_reason", None) == "refusal":
        raise ClaudeUnavailable("Claude declined this request (stop_reason=refusal)")
    text = "".join(getattr(b, "text", "") for b in message.content if getattr(b, "type", "") == "text")
    if not text.strip():
        raise ClaudeUnavailable(f"Claude returned no text (stop_reason={message.stop_reason})")
    return text


class _Ticker:
    """Prints a dot every couple of seconds until the first streamed text arrives."""

    def __init__(self):
        self.waiting = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.wait(2.0):
            if self.waiting:
                ui.stream_write(".")

    def __enter__(self):
        ui.stream_write(f"  Claude ({model()}) is thinking")
        self._thread.start()
        return self

    def first_text(self):
        if self.waiting:
            self.waiting = False
            ui.stream_write("\n\n")

    def __exit__(self, *exc):
        self._stop.set()
        if self.waiting:
            ui.stream_write("\n")


def _cli_env() -> dict:
    """Environment for `claude -p`: no service keys, and no API key, so it uses the subscription."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    for name in list(env):
        if "STRIPE" in name.upper() or name.upper().endswith(("_TEST_KEY", "_SECRET_KEY")):
            env.pop(name)
    return env


def _ask_cli(prompt: str, system, stream: bool) -> str:
    """One headless Claude Code call with no tools, run in an empty temp folder."""
    why = _cli_problem()
    if why:
        raise ClaudeUnavailable(why)
    args = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--tools", "", "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "",
            "--model", model(),
            "--system-prompt", system or "You are a careful senior engineer. Follow the instructions exactly."]
    started = time.monotonic()
    with _cli_slots, tempfile.TemporaryDirectory(prefix="claude-call-") as workdir:
        err_path = os.path.join(workdir, "stderr.txt")
        with open(err_path, "w+", encoding="utf-8", errors="replace") as err:
            try:
                proc = subprocess.Popen(args, cwd=workdir, env=_cli_env(), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8",
                                        errors="replace")
            except OSError as e:
                raise ClaudeUnavailable(f"could not start `claude`: {e}") from None
            killer = threading.Timer(CLI_TIMEOUT, proc.kill)
            killer.start()
            parts, result = [], None
            ticker = _Ticker() if stream else None
            try:
                if ticker:
                    ticker.__enter__()
                proc.stdin.write(prompt)
                proc.stdin.close()
                for line in proc.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") == "stream_event":
                        inner = event.get("event") or {}
                        delta = inner.get("delta") or {}
                        if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                            parts.append(delta.get("text", ""))
                            if ticker:
                                ticker.first_text()
                                ui.stream_write(delta.get("text", ""))
                    elif event.get("type") == "result" or "is_error" in event:
                        result = event
                proc.wait()
            except (OSError, ValueError) as e:
                proc.kill()
                raise ClaudeUnavailable(f"Claude Code call failed: {type(e).__name__}: {e}") from None
            finally:
                killer.cancel()
                if ticker:
                    ticker.__exit__(None, None, None)
            err.seek(0)
            err_text = err.read().strip()
    if time.monotonic() - started >= CLI_TIMEOUT:
        raise ClaudeUnavailable(f"Claude Code did not answer within {CLI_TIMEOUT}s")
    if result is None:
        first = err_text.splitlines()[0][:200] if err_text else f"exit code {proc.returncode}"
        raise ClaudeUnavailable(f"Claude Code gave no answer ({first})")
    if result.get("stop_reason") == "refusal":
        raise ClaudeUnavailable("Claude declined this request (stop_reason=refusal)")
    text = result.get("result") if isinstance(result.get("result"), str) else "".join(parts)
    if result.get("is_error"):
        msg = (text or err_text or "unknown error").strip().splitlines()[0][:200]
        raise ClaudeUnavailable(f"Claude Code error: {msg}")
    if not text.strip():
        text = "".join(parts)
    if not text.strip():
        raise ClaudeUnavailable(f"Claude returned no text (stop_reason={result.get('stop_reason')})")
    if stream:
        ui.stream_write("\n")
        ui.detail(f"(streamed via Claude Code in {time.monotonic() - started:.0f}s, "
                  f"stop_reason={result.get('stop_reason')})")
    return text


def ask(prompt: str, system: str = None, max_tokens: int = 16000, stream: bool = False) -> str:
    """Send one user message; return the full response text. stream=True prints it live (dimmed)."""
    if backend() == "cli":
        return _ask_cli(prompt, system, stream)
    client = _get_client()
    import anthropic
    params = _params(prompt, system, max_tokens)
    started = time.monotonic()
    try:
        if not stream:
            return _text_of(client.messages.create(**params))
        with _Ticker() as ticker, client.messages.stream(**params) as s:
            for text in s.text_stream:
                ticker.first_text()
                ui.stream_write(text)
            message = s.get_final_message()
        ui.stream_write("\n")
        ui.detail(f"(streamed in {time.monotonic() - started:.0f}s, stop_reason={message.stop_reason})")
        return _text_of(message)
    except ClaudeUnavailable:
        raise
    except anthropic.AnthropicError as e:
        raise ClaudeUnavailable(_describe_error(e)) from None
    except Exception as e:  # network stacks can leak non-SDK exceptions; never crash the demo
        raise ClaudeUnavailable(_describe_error(e)) from None


def ping() -> None:
    """Tiny call with the configured model. Raises ClaudeUnavailable on any problem."""
    if backend() == "cli":
        _ask_cli("Reply with the single word OK.", "Answer in one word.", stream=False)
        return
    client = _get_client()
    import anthropic
    try:
        client.messages.create(model=model(), max_tokens=64,
                               messages=[{"role": "user", "content": "Reply with the single word OK."}])
    except anthropic.AnthropicError as e:
        raise ClaudeUnavailable(_describe_error(e)) from None
    except Exception as e:
        raise ClaudeUnavailable(_describe_error(e)) from None


# ---- extraction -----------------------------------------------------------------

_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n(.*?)```", re.S)
_PY_FENCE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.S)


def extract_json(text: str):
    """Strip code fences, take first '{' to last '}', parse. Raises ValueError."""
    body = text
    fences = _FENCE.findall(text)
    if fences:
        body = max(fences, key=len)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        start, end = text.find("{"), text.rfind("}")
        body = text
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in Claude's reply")
    return json.loads(body[start:end + 1])


def extract_python(text: str) -> str:
    """Largest ```python block, else the whole text."""
    blocks = _PY_FENCE.findall(text)
    if not blocks:
        blocks = [b for b in _FENCE.findall(text) if "class " in b]
    if not blocks:
        m = re.search(r"```(?:python|py)\s*\n", text)  # unterminated fence (reply cut off)
        if m:
            blocks = [text[m.end():]]
    code = max(blocks, key=len) if blocks else text
    return code.strip() + "\n"
