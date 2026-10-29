"""Step 2: Claude writes the in-memory fake from the fixtures; it is validated and self-corrected.

    python generate_fake.py [--keep] [--offline]

Fallback: if the AI fake still fails (or Claude is unavailable), use the backup fake if it passes
the current fixtures; otherwise install backup fixtures AND backup fake together (a consistent set).
"""

import argparse
import shutil
import sys

from toolkit import claude, config, fakes, scenarios, ui

SYSTEM = ("You are a senior Python engineer who writes faithful in-memory test doubles of web APIs. "
          "You reply with one complete ```python code block and nothing else.")

PROMPT = """\
Write an in-memory fake of {display} for our tests.

## Gateway interface
{interface}

## Domain notes
{notes}

## The code under test (app.py)
```python
{app_source}
```

## Fixtures: what {display} returns for each scenario (client secrets removed)
Each fixture lists the calls made (args may reference "$stepN.id", the id produced by step N) and
the exact result of each call: kind, http_status, and full JSON body.
{fixtures}

## Requirements
- One self-contained Python 3.10 module, standard library only. No network, no file I/O.
- `from toolkit.gateway import GatewayError` and raise GatewayError(http_status, body) for every error,
  where body is the exact JSON error body (as in the fixtures).
- Define class {fake_class} with exactly these methods and parameter names (callers pass keywords):
{signatures}
  The constructor takes no arguments. All state is in-memory, per instance.
{fake_requirements}- Reproduce every fixture's outcome: for each step the same kind, http_status and every summary
  field (object, status, amount, currency, error type, error code, decline code ...). Return
  realistic full bodies shaped like the fixtures (all fields, nulls included).
- Implement behavior generally (map inputs such as test tokens and amounts to outcomes). Never
  look up fixtures, never hard-code scenario names.
- Return ONE ```python block. The module must start with a docstring.
"""

FIX_PROMPT = """\
Your fake of {display} fails our self-check. Here is the current code:

```python
{source}
```

{problems}

Fix every problem while keeping all the original requirements (stdlib only, no I/O, exact method
signatures, `from toolkit.gateway import GatewayError`, behavior implemented generally). Return the
FULL corrected module as ONE ```python block starting with a docstring.
"""


def fixtures_for_prompt(p):
    out = []
    for name, rec in scenarios.load_records(config.fixtures_dir(p)).items():
        steps = [{"call": s.get("call"), "args": s.get("args"), "kind": s.get("kind"),
                  "http_status": s.get("http_status"), "body": s.get("body")} for s in rec.get("steps", [])]
        out.append(f"### {name}: {rec.get('description', '')}\n{scenarios.compact(steps)}")
    return "\n\n".join(out)


def make_fixer(p):
    def fix(source, problems):
        ui.info("asking Claude for a corrected fake (streaming):")
        text = claude.ask(FIX_PROMPT.format(display=p.DISPLAY_NAME, source=source, problems=problems),
                          system=SYSTEM, max_tokens=64000, stream=True)
        return claude.extract_python(text)
    return fix


def copy_backup_fixtures(p) -> int:
    out_dir = config.fixtures_dir(p)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in list(out_dir.glob("*.json")) + list(out_dir.glob("*.preflaw")):
        old.unlink()
    n = 0
    for f in sorted(config.backup_fixtures_dir(p).glob("*.json")):
        shutil.copy2(f, out_dir / f.name)
        n += 1
    return n


def fall_back(p, reason) -> bool:
    """Backup fake if it passes the current fixtures, else backup fixtures + backup fake together."""
    ui.warn(reason)
    backup_src = fakes.read(config.backup_fake_path(p))
    if backup_src is None:
        ui.fail(f"no backup fake at {config.rel(config.backup_fake_path(p))}")
        return False
    fakes.install(config.fake_path(p), backup_src)
    ui.info("trying the backup fake against the current fixtures ...")
    passed, _, _ = fakes.run_fake_suite()
    if passed:
        ui.ok("using the backup fake written in rehearsal: it passes the current fixtures")
        return True
    n = copy_backup_fixtures(p)
    ui.warn(f"the backup fake doesn't match these fixtures, so installing the backup set together: "
            f"{n} backup fixtures + backup fake")
    passed, count, out = fakes.run_fake_suite()
    if passed:
        ui.ok("backup fixtures + backup fake installed; the fake suite passes")
        return True
    ui.fail(f"the backup set itself fails ({count} problems). Run: python preflight.py")
    ui.detail(out[-1500:])
    return False


def finish(p) -> None:
    path = config.fake_path(p)
    lines = len((fakes.read(path) or "").splitlines())
    ui.say()
    ui.ok(f"{lines} lines of code {ui.SYM['arrow']} {config.rel(path)}")
    ui.say(ui.bold(f"  Open {config.rel(path)}"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="keep the AI version even if it fails the self-check")
    parser.add_argument("--offline", action="store_true", help="don't call Claude; install the backup fake")
    args = parser.parse_args(argv)
    config.load_env()
    p = config.load_provider()
    ui.banner(2, f"Claude writes a fake {p.DISPLAY_NAME}")

    fx_dir = config.fixtures_dir(p)
    if p.undo_flaw(fx_dir):
        ui.warn("a planted flaw was in the fixtures; restored the original fixture before regenerating the fake")
    if not scenarios.load_records(fx_dir):
        n = copy_backup_fixtures(p)
        ui.warn(f"no fixtures in {config.rel(fx_dir)}; using {n} backup fixtures (run generate_fixtures.py first)")

    why = "--offline" if args.offline else claude.problem()
    if why:
        ok = fall_back(p, f"Claude unavailable ({why}): using the backup fake")
        finish(p)
        return 0 if ok else 1

    prompt = PROMPT.format(display=p.DISPLAY_NAME, interface=p.INTERFACE_DOC, notes=p.PROMPT_NOTES,
                           app_source=fakes.read(config.ROOT / "app.py") or "", fixtures=fixtures_for_prompt(p),
                           fake_class=p.FAKE_CLASS, signatures=fakes.signature_text(p),
                           fake_requirements="".join(f"- {r}\n" for r in getattr(p, "FAKE_REQUIREMENTS", [])))
    ui.info(f"sending interface, domain notes, app.py and {len(scenarios.load_records(fx_dir))} fixtures "
            f"to {claude.model()}; streaming the code:")
    try:
        source = claude.extract_python(claude.ask(prompt, system=SYSTEM, max_tokens=64000, stream=True))
    except claude.ClaudeUnavailable as e:
        ok = fall_back(p, f"Claude unavailable ({e}): using the backup fake")
        finish(p)
        return 0 if ok else 1

    ui.say()
    ui.info("validating: parse, import, class, signatures, then the fake test suite")
    passed, source, problems = fakes.validate_and_install(source, p, make_fixer(p), rounds=2)
    if passed:
        ui.ok("Claude's fake passes; installed")
        finish(p)
        return 0
    if args.keep:
        fakes.install(config.fake_path(p), source, keep_prev=False)
        ui.warn("--keep: keeping Claude's version even though it fails the self-check")
        ui.detail(problems[-1200:])
        finish(p)
        return 0
    ok = fall_back(p, "Claude's fake still fails after 2 self-fix rounds: falling back to the backup")
    finish(p)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
