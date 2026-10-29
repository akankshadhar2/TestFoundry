"""Loading, validating, installing and testing fake gateway modules (shared by the scripts).

Claude-written code is only ever imported in a subprocess with a timeout, so a broken or hanging
fake can't take a demo script down with it.
"""

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

from toolkit import config, scenarios, ui

FLAW_START = "# >>> DEMO FLAW >>>"
FLAW_END = "# <<< DEMO FLAW <<<"

# The gateway "port" from DESIGN.md 4.1. A provider may override it with GATEWAY_METHODS.
DEFAULT_INTERFACE = {
    "create_payment": (["self", "amount", "currency", "payment_method"], {}),
    "refund_payment": (["self", "payment_id", "amount"], {"amount": None}),
}


def interface(provider) -> dict:
    return getattr(provider, "GATEWAY_METHODS", None) or DEFAULT_INTERFACE


def signature_text(provider) -> str:
    lines = []
    for meth, (names, defaults) in interface(provider).items():
        params = ", ".join(f"{n}={defaults[n]!r}" if n in defaults else n for n in names)
        lines.append(f"    def {meth}({params})")
    return "\n".join(lines)
BANNED_IMPORTS = {"stripe", "requests", "urllib", "urllib3", "http", "httpx", "socket",
                  "subprocess", "aiohttp", "anthropic"}


# ---- flaw markers ---------------------------------------------------------------

def has_flaw(source: str) -> bool:
    return FLAW_START in source


def strip_flaw(source: str) -> str:
    pattern = re.compile(r"\n*" + re.escape(FLAW_START) + r".*?" + re.escape(FLAW_END) + r"[^\n]*\n?", re.S)
    return pattern.sub("\n", source).rstrip() + "\n"


# ---- loading --------------------------------------------------------------------

def load_module(path, name: str = None):
    """Import a fake module from a file path (in-process: use only for our own files)."""
    path = Path(path)
    name = name or f"_fake_{path.stem}_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_against_fixtures(fake_cls, fixtures: dict, provider) -> list:
    """In-process: [(scenario name, diffs)] for every scenario whose fixture the fake doesn't match."""
    bad = []
    for sc in provider.SCENARIOS:
        fx = fixtures.get(sc["name"])
        if fx is None:
            bad.append((sc["name"], "missing fixture"))
            continue
        diffs = scenarios.compare(fx, scenarios.run_scenario(fake_cls(), sc, provider), provider)
        if diffs:
            bad.append((sc["name"], diffs))
    return bad


# ---- structural validation ------------------------------------------------------

_CHECK_SNIPPET = r"""
import importlib.util, inspect, json, sys
path, cls_name, iface = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
problems = []
try:
    spec = importlib.util.spec_from_file_location("_candidate_fake", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
except BaseException as e:
    print(json.dumps(["import failed: %s: %s" % (type(e).__name__, e)])); sys.exit(0)
cls = getattr(mod, cls_name, None)
if not inspect.isclass(cls):
    print(json.dumps(["module has no class %s" % cls_name])); sys.exit(0)
for meth, (names, defaults) in iface.items():
    fn = getattr(cls, meth, None)
    if fn is None:
        problems.append("%s.%s is missing" % (cls_name, meth)); continue
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError) as e:
        problems.append("cannot inspect %s: %s" % (meth, e)); continue
    got = list(sig.parameters)
    if got != names:
        problems.append("%s%s has parameters %s; must be exactly (%s)" % (meth, "", got, ", ".join(names)))
        continue
    for pname, default in defaults.items():
        if sig.parameters[pname].default is not default:
            problems.append("%s: parameter %s must default to %r" % (meth, pname, default))
try:
    cls()
except BaseException as e:
    problems.append("%s() failed: %s: %s" % (cls_name, type(e).__name__, e))
print(json.dumps(problems))
"""


def static_problems(source: str, provider) -> list:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"syntax error at line {e.lineno}: {e.msg}"]
    problems = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for n in names:
            if n.split(".")[0] in BANNED_IMPORTS:
                problems.append(f"imports '{n}' (must be stdlib-only, no network or file I/O)")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            problems.append(f"calls open() at line {node.lineno} (no file I/O allowed)")
    # Several scenario names as literals means per-scenario lookups instead of real behavior.
    # (One alone can be legitimate: "insufficient_funds" is also a decline code.)
    scenario_names = {s["name"] for s in provider.SCENARIOS}
    found = sorted({n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in scenario_names})
    if len(found) >= 3:
        problems.append(f"mentions scenario names {found}: implement behavior generally "
                        "(map inputs to outcomes), never per scenario")
    return sorted(set(problems))


def structural_problems(source: str, provider, timeout: int = 30) -> list:
    """Static checks, then import + class + exact signature checks in a subprocess."""
    problems = static_problems(source, provider)
    if problems:
        return problems
    candidate = config.generated_dir(provider) / ".candidate_fake.py"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(source, encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CHECK_SNIPPET, str(candidate), provider.FAKE_CLASS, json.dumps(interface(provider))],
            cwd=config.ROOT, capture_output=True, text=True, timeout=timeout)
        out = proc.stdout.strip().splitlines()
        return json.loads(out[-1]) if out else [f"import check produced no output: {proc.stderr.strip()[-300:]}"]
    except subprocess.TimeoutExpired:
        return [f"importing the module took longer than {timeout}s (infinite loop at import time?)"]
    except (ValueError, OSError) as e:
        return [f"import check failed: {e}"]
    finally:
        candidate.unlink(missing_ok=True)


# ---- running the fake suite -----------------------------------------------------

def run_fake_suite(timeout: int = 180):
    """Run `python -m pytest -q --backend=fake` in a subprocess. Returns (passed, n_problems, output_tail)."""
    cmd = [sys.executable, "-m", "pytest", "-q", "--backend=fake", "--color=no", "-p", "no:cacheprovider"]
    try:
        proc = subprocess.run(cmd, cwd=config.ROOT, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, 1, f"the fake test suite did not finish within {timeout}s (the fake may hang)"
    out = (proc.stdout or "") + (proc.stderr or "")
    n = sum(int(x) for x in re.findall(r"(\d+) (?:failed|errors?)\b", out.splitlines()[-1] if out.strip() else ""))
    if proc.returncode != 0 and n == 0:
        n = 1
    return proc.returncode == 0, n, tail(out)


def tail(text: str, lines: int = 80, chars: int = 6000) -> str:
    t = "\n".join(text.strip().splitlines()[-lines:])
    return t[-chars:]


# ---- installing -----------------------------------------------------------------

def read(path) -> str:
    path = Path(path)
    return path.read_text(encoding="utf-8") if path.is_file() else None


def install(path, source: str, keep_prev: bool = True) -> None:
    """Write the fake; keep the previous version as <file>.prev."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if keep_prev and path.is_file():
        prev = path.with_name(path.name + ".prev")
        prev.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(source, encoding="utf-8")


def validate_and_install(source: str, provider, fix, rounds: int = 2, extra_check=None):
    """Structural checks -> install -> fake suite; up to `rounds` Claude self-fix rounds.

    fix(source, problem_text) -> new source (may raise ClaudeUnavailable).
    Returns (ok, source, last_problem_text). The original file is preserved as .prev once.
    """
    from toolkit.claude import ClaudeUnavailable

    path = config.fake_path(provider)
    first_write = True
    problem_text = ""
    for attempt in range(rounds + 1):
        problems = structural_problems(source, provider)
        if extra_check:
            problems += extra_check(source)
        if problems:
            problem_text = "Structural problems:\n" + "\n".join(f"- {p}" for p in problems)
            count = len(problems)
            for p in problems[:6]:
                ui.detail(p)
        else:
            ui.ok("structure OK: parses, imports, class and signatures match")
            install(path, source, keep_prev=first_write)
            first_write = False
            ui.info("running the fake test suite ...")
            passed, count, out = run_fake_suite()
            if passed:
                ui.ok("fake passes every fixture and app test")
                return True, source, ""
            problem_text = "pytest --backend=fake output (tail):\n" + out
        if attempt == rounds:
            break
        ui.warn(f"AI self-check found {count} problem{'s' if count != 1 else ''}, fixing{ui.SYM['ellipsis']} "
                f"(round {attempt + 1}/{rounds})")
        try:
            source = fix(source, problem_text)
        except ClaudeUnavailable as e:
            ui.warn(f"Claude unavailable during self-fix: {e}")
            break
    return False, source, problem_text
