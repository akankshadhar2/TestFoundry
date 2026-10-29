"""Optional, before step 3: plant one realistic mistake in the fake so step 4 is guaranteed to show red.

    python inject_flaw.py          # plant (idempotent)
    python inject_flaw.py --undo   # remove the flaw block and restore the original fixture
"""

import argparse
import sys

from toolkit import config, fakes, ui


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--undo", action="store_true", help="remove the planted flaw")
    args = parser.parse_args(argv)
    config.load_env()
    p = config.load_provider()
    ui.banner("OPTIONAL", "Undo the planted flaw" if args.undo else "Plant a realistic flaw in the fake")

    path, fx_dir = config.fake_path(p), config.fixtures_dir(p)
    source = fakes.read(path)
    if source is None:
        ui.fail(f"no generated fake at {config.rel(path)}. Run: python generate_fake.py  "
                "(or python reset.py --from-backup)")
        return 1

    if args.undo:
        if fakes.has_flaw(source):
            fakes.install(path, fakes.strip_flaw(source), keep_prev=False)
            ui.ok(f"removed the DEMO FLAW block from {config.rel(path)}")
        else:
            ui.info("no flaw block in the fake")
        if p.undo_flaw(fx_dir):
            ui.ok("restored the original fixture from its .preflaw copy")
        else:
            ui.info("no .preflaw fixture to restore")
        return 0

    if fakes.has_flaw(source):
        ui.info("the flaw is already planted in the fake")
    else:
        block = f"\n\n{fakes.FLAW_START}\n{p.FLAW_CODE.strip()}\n{fakes.FLAW_END}\n"
        fakes.install(path, source.rstrip() + block, keep_prev=False)
        ui.ok(f"appended the DEMO FLAW block to {config.rel(path)}")
    try:
        ui.ok(p.apply_flaw(fx_dir) + " (so step 3 stays green)")
    except FileNotFoundError as e:
        ui.fail(f"{e}. Run: python generate_fixtures.py  (or python reset.py --from-backup)")
        return 1

    ui.say()
    ui.say(ui.bold(f'  Say: "To make sure we see it, {p.FLAW_DESCRIPTION}"'))
    ui.say("  Next: python -m pytest --backend=fake   then   python -m pytest --backend=real")
    return 0


if __name__ == "__main__":
    sys.exit(main())
