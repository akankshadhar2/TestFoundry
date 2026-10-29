"""After a good rehearsal: freeze the generated fixtures + fake + step-0 recordings as the new backup.

    python save_backup.py [--yes]

Refuses if the fake contains the planted flaw or fails the fake suite.
"""

import argparse
import shutil
import sys

from toolkit import config, fakes, scenarios, ui


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    args = parser.parse_args(argv)
    config.load_env()
    p = config.load_provider()
    ui.banner(None, "Save the current generated set as the backup")

    fake_file, fx_dir = config.fake_path(p), config.fixtures_dir(p)
    source = fakes.read(fake_file)
    fixtures = sorted(fx_dir.glob("*.json"))
    if source is None or not fixtures:
        ui.fail("nothing to save: run the pipeline first (generate_fixtures.py, generate_fake.py)")
        return 1
    if fakes.has_flaw(source) or list(fx_dir.glob("*.preflaw")):
        ui.fail("refusing: the planted DEMO FLAW is present. Run python repair_fake.py or python inject_flaw.py --undo")
        return 1
    missing = [s["name"] for s in p.SCENARIOS if not (fx_dir / f"{s['name']}.json").is_file()]
    if missing:
        ui.fail(f"refusing: fixtures missing for {', '.join(missing)}")
        return 1
    ui.info("running the fake suite ...")
    passed, count, out = fakes.run_fake_suite()
    if not passed:
        ui.fail(f"refusing: the fake fails {count} test(s)")
        ui.detail(out[-1200:])
        return 1
    ui.ok("fake passes every fixture and app test")

    recordings = sorted(config.record_dir(p).glob("*.json"))
    real_recs = [r for r in recordings if scenarios.load_json(r).get("source") == "recorded"]
    dest = config.backup_dir(p)
    ui.info(f"will replace {config.rel(dest)}/: {len(fixtures)} fixtures, the fake"
            + (f", {len(real_recs)} real recordings" if real_recs else " (keeping the sample recordings)"))
    if not args.yes:
        if not sys.stdin.isatty():
            ui.fail("not a terminal: rerun with --yes to confirm")
            return 1
        try:
            answer = input("  Overwrite the backup? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            ui.info("cancelled; nothing changed")
            return 0

    bfx = config.backup_fixtures_dir(p)
    bfx.mkdir(parents=True, exist_ok=True)
    for old in bfx.glob("*.json"):
        old.unlink()
    for f in fixtures:
        shutil.copy2(f, bfx / f.name)
    shutil.copy2(fake_file, config.backup_fake_path(p))
    ui.ok(f"saved {len(fixtures)} fixtures and the fake")
    if real_recs:
        brec = config.backup_recordings_dir(p)
        brec.mkdir(parents=True, exist_ok=True)
        for old in brec.glob("*.json"):
            old.unlink()
        for r in real_recs:
            shutil.copy2(r, brec / r.name)
        ui.ok(f"saved {len(real_recs)} real recordings (replacing the samples)")
    ui.say(ui.bold("  Backup updated. Next: python reset.py"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
