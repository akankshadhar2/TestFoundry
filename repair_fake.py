"""Step 5: real responses are the source of truth. Correct the fixtures, then Claude repairs the fake.

    python repair_fake.py

1. Load recordings/<provider>/real_run.json (from --backend=real).
2. Compare each fixture with its real record and print every mismatch.
3. Deterministically overwrite wrong fixtures with the real records.
4. Claude (streamed) rewrites the fake; same validation loop as generate_fake.py.
Fallbacks: strip a planted flaw mechanically; try the backup fake; else restore the previous fake.
"""

import argparse
import sys

from toolkit import claude, config, fakes, scenarios, ui

SYSTEM = ("You are a senior Python engineer who maintains faithful in-memory test doubles of web APIs. "
          "You reply with one complete ```python code block and nothing else.")

PROMPT = """\
Our in-memory fake of {display} disagrees with the REAL {display} API. Real {display} is the source of
truth. Rewrite the fake so it behaves like the real API.

## Gateway interface
{interface}

## Current fake
```python
{source}
```

## Mismatches found (AI fixture expected vs what real {display} did)
{report}

## Real responses for the mismatched scenarios (client secrets removed)
{real_bodies}

## Requirements
- Change behavior so every mismatched step produces exactly what real {display} returned (kind,
  http_status, and the summary fields: object, status, amount, currency, error type, error code,
  decline code). Keep everything that already matched.
- Remove anything that contradicts real behavior, INCLUDING any block between "{flaw_start}" and
  "{flaw_end}" markers: delete that block completely, markers included.
- Keep: one self-contained Python 3.10 module, stdlib only, no network or file I/O,
  `from toolkit.gateway import GatewayError`, class {fake_class} with exactly these methods:
{signatures}
- Implement behavior generally, never per scenario name; deterministic IDs with the real prefixes.
- Return the FULL corrected module as ONE ```python block starting with a docstring.
"""

FIX_PROMPT = """\
Your repaired fake of {display} still fails our self-check. Current code:

```python
{source}
```

{problems}

Fix every problem. Keep the original requirements and make sure no "{flaw_start}" block remains.
Return the FULL corrected module as ONE ```python block starting with a docstring.
"""

NEXT = "Now rerun: python -m pytest --backend=fake  and  python -m pytest --backend=real"


def flaw_check(source):
    return ["the DEMO FLAW block is still present: delete it, markers included"] if fakes.has_flaw(source) else []


def try_backup(p) -> bool:
    backup_src = fakes.read(config.backup_fake_path(p))
    if backup_src is None:
        return False
    fakes.install(config.fake_path(p), backup_src, keep_prev=False)
    passed, _, _ = fakes.run_fake_suite()
    return passed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    config.load_env()
    p = config.load_provider()
    ui.banner(5, f"Repair the fake from real {p.DISPLAY_NAME}")

    # 1. real responses
    run_path = config.real_run_path(p)
    if not run_path.is_file():
        ui.fail(f"no real responses yet ({config.rel(run_path)} missing).")
        ui.info("Run: python -m pytest --backend=real   (it saves the real responses), then rerun this.")
        ui.info("No network? python -m pytest --backend=replay uses the rehearsal recording, if present.")
        return 1
    try:
        real = scenarios.load_json(run_path).get("records", {})
    except (OSError, ValueError) as e:
        ui.fail(f"could not read {config.rel(run_path)}: {e}")
        return 1
    broken = scenarios.setup_problem(real)
    if broken:
        ui.fail(f"every call in {config.rel(run_path)} failed with the same error. That is a setup problem, "
                "not real behavior, so the fixtures were left alone.")
        ui.info(f"{p.DISPLAY_NAME} said: {broken}")
        ui.info("Fix the setup, then: python -m pytest --backend=real   and rerun this.")
        return 1
    fake_file = config.fake_path(p)
    original = fakes.read(fake_file)
    if original is None:
        ui.fail(f"no generated fake at {config.rel(fake_file)}. Run: python generate_fake.py")
        return 1
    ui.info(f"real responses from {config.rel(run_path)} "
            f"({len(real)} scenarios, recorded {scenarios.load_json(run_path).get('recorded_at', '?')})")

    # 2. compare fixtures with reality
    fx_dir = config.fixtures_dir(p)
    fixtures = scenarios.load_records(fx_dir)
    mismatched, report_parts = {}, []
    for sc in p.SCENARIOS:
        name = sc["name"]
        if name not in real:
            ui.info(f"{name}: no real response recorded (skipped)")
            continue
        fx = fixtures.get(name)
        diffs = scenarios.compare(fx, real[name], p) if fx else [{"index": 0, "call": "-", "expected": None,
                                                                  "actual": None, "fields": []}]
        if diffs:
            mismatched[name] = real[name]
            text = scenarios.format_diff(name, diffs, "AI expected", f"real {p.DISPLAY_NAME} did")
            report_parts.append(text)
            ui.fail(text.splitlines()[0])
            for line in text.splitlines()[1:]:
                ui.detail(line)
        else:
            ui.ok(f"{name}: fixture matches real {p.DISPLAY_NAME}")

    # 3. nothing wrong?
    if not mismatched:
        ui.info("every fixture matches reality; checking the fake ...")
        passed, count, out = fakes.run_fake_suite()
        if passed:
            ui.ok("Nothing to repair: fixtures match real responses and the fake passes.")
            return 0
        ui.warn(f"fixtures are right but the fake fails {count} test(s): asking Claude to fix the fake")
        report_parts.append("The fixtures are correct, but the fake fails these tests:\n" + out)

    # 4. correct fixtures deterministically
    for name, rec in mismatched.items():
        corrected = dict(rec, scenario=name, source="real-run",
                         description=(fixtures.get(name) or {}).get("description", rec.get("description", "")))
        scenarios.save_json(fx_dir / f"{name}.json", corrected)
        (fx_dir / f"{name}.json.preflaw").unlink(missing_ok=True)
        ui.ok(f"fixture {name} corrected from real {p.DISPLAY_NAME}")

    # 5-6. Claude repairs the fake, with the same validation loop
    report = "\n\n".join(report_parts)
    real_bodies = "\n\n".join(f"### {n}\n{scenarios.compact(r.get('steps'))}" for n, r in mismatched.items())
    fmt = dict(display=p.DISPLAY_NAME, fake_class=p.FAKE_CLASS, flaw_start=fakes.FLAW_START,
               flaw_end=fakes.FLAW_END)
    repaired = False
    why = claude.problem()
    if why:
        ui.warn(f"Claude unavailable ({why})")
    else:
        ui.say()
        ui.info(f"asking {claude.model()} to repair the fake (streaming):")
        try:
            text = claude.ask(PROMPT.format(interface=p.INTERFACE_DOC, source=original, report=report,
                                            real_bodies=real_bodies or "(none)",
                                            signatures=fakes.signature_text(p), **fmt),
                              system=SYSTEM, max_tokens=64000, stream=True)
            source = claude.extract_python(text)

            def fix(src, problems):
                ui.info("asking Claude for a corrected fake (streaming):")
                return claude.extract_python(claude.ask(FIX_PROMPT.format(source=src, problems=problems, **fmt),
                                                        system=SYSTEM, max_tokens=64000, stream=True))

            ui.say()
            ui.info("validating: parse, import, class, signatures, no flaw block, then the fake test suite")
            repaired, _, _ = fakes.validate_and_install(source, p, fix, rounds=2, extra_check=flaw_check)
        except claude.ClaudeUnavailable as e:
            ui.warn(f"Claude unavailable ({e})")

    # 7. fallbacks
    if repaired:
        ui.ok("Claude repaired the fake; it now agrees with the real responses")
    else:
        if fakes.has_flaw(original):
            ui.warn("falling back: removing the planted DEMO FLAW block mechanically and retesting")
            fakes.install(fake_file, fakes.strip_flaw(original), keep_prev=False)
            repaired, _, _ = fakes.run_fake_suite()
            if repaired:
                ui.ok("flaw removed mechanically; the fake now passes the corrected fixtures")
        if not repaired:
            ui.warn("trying the backup fake against the corrected fixtures ...")
            repaired = try_backup(p)
            if repaired:
                ui.ok("the backup fake passes the corrected fixtures; using it")
        if not repaired:
            fakes.install(fake_file, original, keep_prev=False)
            ui.fail("could not produce a passing fake; restored the previous fake "
                    "(the corrected fixtures are kept)")
            ui.info("Options: rerun python repair_fake.py   or   python generate_fake.py  "
                    "(rebuilds the fake from the corrected fixtures)   or   python reset.py --from-backup")
            return 1

    # 8.
    ui.say()
    ui.say(ui.bold(f"  {NEXT}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
