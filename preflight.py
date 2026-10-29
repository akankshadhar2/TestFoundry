"""Pre-demo checklist: Python, packages, keys, Stripe and Claude reachable, backups, current state.

    python preflight.py [--no-ping]

Never prints key values. Exits 1 only for problems the fallbacks can't cover.
"""

import argparse
import importlib
import sys

from toolkit import config, fakes, scenarios, ui

problems, degraded = [], []


def check(ok_: bool, good: str, bad: str, fix: str, blocking: bool = True) -> bool:
    if ok_:
        ui.ok(good)
    else:
        (ui.fail if blocking else ui.warn)(bad)
        ui.detail(f"fix: {fix}")
        (problems if blocking else degraded).append(bad)
    return ok_


def section(title: str) -> None:
    ui.say()
    ui.say(ui.bold(f"  {title}"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-ping", action="store_true", help="don't make the tiny Claude test call")
    args = parser.parse_args(argv)
    config.load_env()
    ui.banner("PREFLIGHT", "Pre-demo checklist")

    section("Environment")
    v = sys.version_info
    check(v >= (3, 10), f"Python {v.major}.{v.minor}.{v.micro}", f"Python {v.major}.{v.minor} is too old",
          "install Python 3.10 or newer")
    for pkg in ("stripe", "anthropic", "pytest"):
        try:
            mod = importlib.import_module(pkg)
            ui.ok(f"{pkg} {getattr(mod, '__version__', getattr(mod, 'VERSION', ''))}")
        except ImportError:
            check(False, "", f"package '{pkg}' is not installed", "pip install -r requirements.txt",
                  blocking=(pkg == "pytest"))
    try:
        p = config.load_provider()
    except Exception as e:
        check(False, "", f"provider '{config.provider_name()}' failed to load: {e}",
              "check DEMO_PROVIDER in .env and providers/<name>_provider.py")
        return finish()
    ui.ok(f"provider: {p.DISPLAY_NAME} (DEMO_PROVIDER={config.provider_name()})")
    ui.info(f".env {'found' if (config.ROOT / '.env').is_file() else 'not found (using environment only)'}")

    section("Claude")
    from toolkit import claude
    why = claude.problem()
    if check(why is None, f"Claude via {claude.describe()}", f"Claude unavailable: {why}",
             "install Claude Code and log in (free with a Claude subscription), or set ANTHROPIC_API_KEY "
             "in .env (steps 1, 2, 5 will use backups)",
             blocking=False):
        if args.no_ping:
            ui.info(f"model {claude.model()} (ping skipped: --no-ping)")
        else:
            try:
                claude.ping()
                ui.ok(f"Claude reachable with model {claude.model()}")
            except claude.ClaudeUnavailable as e:
                fix = ("run `claude` once in a terminal and log in; check CLAUDE_MODEL in .env"
                       if claude.backend() == "cli" else "check the key, network, and CLAUDE_MODEL in .env")
                check(False, "", f"Claude ping failed with model {claude.model()}: {e}", fix, blocking=False)

    section(p.DISPLAY_NAME)
    kp = p.key_problem()
    if check(kp is None, f"{p.ENV_KEY} is a test key", f"{kp}",
             f"set {p.ENV_KEY} to your test secret key in .env (steps 0 and 4 need it; "
             "fallbacks: recordings + --backend=replay)", blocking=False):
        try:
            ui.ok(f"{p.DISPLAY_NAME}: {p.check_real_access()}")
        except Exception as e:
            check(False, "", f"{p.DISPLAY_NAME} not reachable: {e}",
                  "check the network / key; on stage use --backend=replay", blocking=False)

    section("Backup set (the fallbacks)")
    bfake, bfx, brec = config.backup_fake_path(p), config.backup_fixtures_dir(p), config.backup_recordings_dir(p)
    check(bfake.is_file(), f"backup fake {config.rel(bfake)}", f"missing {config.rel(bfake)}",
          "restore it from git")
    names = [s["name"] for s in p.SCENARIOS]
    missing = [n for n in names if not (bfx / f"{n}.json").is_file()]
    check(not missing, f"{len(names)} backup fixtures", f"backup fixtures missing: {', '.join(missing)}",
          "python tools/build_backup.py")
    missing_rec = [n for n in p.RECORD_SCENARIOS if not (brec / f"{n}.json").is_file()]
    check(not missing_rec, f"{len(p.RECORD_SCENARIOS)} backup recordings",
          f"backup recordings missing: {', '.join(missing_rec)}", "python tools/build_backup.py")
    if bfake.is_file() and not missing:
        try:
            cls = getattr(fakes.load_module(bfake), p.FAKE_CLASS)
            bad = fakes.run_against_fixtures(cls, scenarios.load_records(bfx), p)
            check(not bad, "backup fake reproduces every backup fixture (consistent set)",
                  f"backup set inconsistent: {', '.join(n for n, _ in bad)}",
                  "python tools/build_backup.py  (or save_backup.py after a good rehearsal)")
        except Exception as e:
            check(False, "", f"backup fake failed to load: {type(e).__name__}: {e}", "restore it from git")

    section("Current state")
    fx = scenarios.load_records(config.fixtures_dir(p))
    ui.info(f"fixtures: {len(fx)}/{len(names)} in {config.rel(config.fixtures_dir(p))}/"
            + (f"  (sources: {', '.join(sorted({r.get('source', '?') for r in fx.values()}))})" if fx else ""))
    src = fakes.read(config.fake_path(p))
    ui.info(f"fake: {'present' if src else 'not generated yet'}"
            + (" -- DEMO FLAW PLANTED" if src and fakes.has_flaw(src) else ""))
    recs = scenarios.load_records(config.record_dir(p))
    ui.info(f"step-0 recordings: {len(recs)}"
            + (f" ({', '.join(sorted({r.get('source', '?') for r in recs.values()}))})" if recs else ""))
    rr = config.real_run_path(p)
    if rr.is_file():
        try:
            data = scenarios.load_json(rr)
            ui.ok(f"real_run.json present ({len(data.get('records', {}))} scenarios, "
                  f"{data.get('recorded_at', '?')}): --backend=replay available")
        except (OSError, ValueError):
            check(False, "", "real_run.json is unreadable", "rerun python -m pytest --backend=real", blocking=False)
    else:
        check(False, "", "no real_run.json: --backend=replay is not available yet",
              "run python -m pytest --backend=real once during rehearsal", blocking=False)
    return finish()


def finish() -> int:
    ui.say()
    if problems:
        ui.fail(f"NOT READY: {len(problems)} problem(s)")
        for prob in problems:
            ui.detail(prob)
        return 1
    if degraded:
        ui.ok(ui.bold("READY") + f" (with fallbacks: {len(degraded)} warning(s) above)")
    else:
        ui.ok(ui.bold("READY"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
