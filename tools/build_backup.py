"""Regenerate backup fixtures and sample recordings by running every scenario through the backup fake.

    python tools/build_backup.py

This guarantees the backup set is internally consistent (the backup fake passes the backup
fixtures by construction). After a good rehearsal, prefer `python save_backup.py`, which
replaces these with real recordings and Claude's output.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from toolkit import config, fakes, scenarios, ui  # noqa: E402

SAMPLE_SOURCE = "sample (not a real recording)"


def main() -> int:
    p = config.load_provider()
    ui.banner(None, f"Build {p.DISPLAY_NAME} backup set from the hand-written fake")
    fake_file = config.backup_fake_path(p)
    if not fake_file.is_file():
        ui.fail(f"{config.rel(fake_file)} not found: write the backup fake first")
        return 1
    fake_cls = getattr(fakes.load_module(fake_file), p.FAKE_CLASS)
    stamp = scenarios.now_iso()
    fx_dir, rec_dir = config.backup_fixtures_dir(p), config.backup_recordings_dir(p)
    for old in list(fx_dir.glob("*.json")) + list(rec_dir.glob("*.json")):
        old.unlink()
    for sc in p.SCENARIOS:
        record = scenarios.run_scenario(fake_cls(), sc, p)
        fixture = {"scenario": sc["name"], "description": sc.get("description", ""), "source": "backup",
                   "generated_at": stamp, "steps": record["steps"]}
        scenarios.save_json(fx_dir / f"{sc['name']}.json", fixture)
        ui.ok(f"fixture   {sc['name']:<24} {scenarios.outcome_line(fixture, p)}")
        if sc["name"] in p.RECORD_SCENARIOS:
            sample = dict(fixture, source=SAMPLE_SOURCE)
            scenarios.save_json(rec_dir / f"{sc['name']}.json", sample)
            ui.ok(f"recording {sc['name']:<24} (labeled '{SAMPLE_SOURCE}')")
    bad = fakes.run_against_fixtures(fake_cls, scenarios.load_records(fx_dir), p)
    if bad:
        ui.fail(f"backup fake does not reproduce: {[n for n, _ in bad]}")
        return 1
    ui.ok("backup fake reproduces every backup fixture")
    return 0


if __name__ == "__main__":
    sys.exit(main())
