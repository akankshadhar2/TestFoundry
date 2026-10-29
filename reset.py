"""Wipe generated output. --from-backup installs the backup fixtures + fake (instantly demo-ready).

    python reset.py [--from-backup]

Never touches recordings/ (step-0 recordings and real_run.json survive).
"""

import argparse
import shutil
import sys

from toolkit import config, scenarios, ui


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-backup", action="store_true", help="copy backup fixtures + fake into place")
    args = parser.parse_args(argv)
    config.load_env()
    p = config.load_provider()
    ui.banner(None, "Reset" + (" from backup" if args.from_backup else ""))

    gen = config.generated_dir(p)
    if gen.exists():
        shutil.rmtree(gen)
        ui.ok(f"deleted {config.rel(gen)}/")
    else:
        ui.info(f"{config.rel(gen)}/ was already empty")

    if args.from_backup:
        src_fx, src_fake = config.backup_fixtures_dir(p), config.backup_fake_path(p)
        files = sorted(src_fx.glob("*.json"))
        if not files or not src_fake.is_file():
            ui.fail(f"backup set incomplete in {config.rel(config.backup_dir(p))}/ (run python preflight.py)")
            return 1
        dst_fx = config.fixtures_dir(p)
        dst_fx.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dst_fx / f.name)
        shutil.copy2(src_fake, config.fake_path(p))
        ui.ok(f"installed {len(files)} backup fixtures {ui.SYM['arrow']} {config.rel(dst_fx)}/")
        ui.ok(f"installed backup fake {ui.SYM['arrow']} {config.rel(config.fake_path(p))}")
        ui.say(ui.bold("  Demo-ready: continue with  python -m pytest --backend=fake"))
    else:
        ui.say(ui.bold("  Clean slate: start with  python record.py"))

    recs = scenarios.load_records(config.record_dir(p))
    ui.info(f"kept recordings/: {len(recs)} step-0 recordings, real_run.json "
            f"{'present' if config.real_run_path(p).is_file() else 'absent'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
