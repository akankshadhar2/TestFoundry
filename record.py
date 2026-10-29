"""Step 0: record a few real calls against the service (test mode) and show how complex they are.

    python record.py [--offline]

Fallback (no key, --offline, network trouble): reuse existing recordings, else copy the backup
sample recordings with a loud warning.
"""

import argparse
import shutil
import sys

from toolkit import config, scenarios, ui


def record_real(p) -> bool:
    try:
        gateway = p.make_real_gateway()
    except Exception as e:
        ui.warn(f"can't create the real client: {e}")
        return False
    by_name = {s["name"]: s for s in p.SCENARIOS}
    out_dir = config.record_dir(p)
    records = {}
    for name in p.RECORD_SCENARIOS:
        ui.info(f"calling real {p.DISPLAY_NAME}: {name} ...")
        try:
            rec = scenarios.run_scenario(gateway, by_name[name], p)
        except Exception as e:
            ui.warn(f"{name}: unexpected {type(e).__name__}: {e}")
            return False
        problem = p.transport_problem(rec)
        if problem:
            ui.warn(f"{name}: {problem}")
            return False
        rec.update({"source": "recorded", "generated_at": scenarios.now_iso()})
        records[name] = rec
    broken = scenarios.setup_problem(records)
    if broken:
        ui.fail(f"every {p.DISPLAY_NAME} call failed with the same error, so this is a setup problem, "
                f"not real behavior. Nothing was saved. {p.DISPLAY_NAME} said: {broken}")
        return False
    for name, rec in records.items():
        scenarios.save_json(out_dir / f"{name}.json", rec)
        ui.ok(f"recorded {config.rel(out_dir / f'{name}.json')}")
    return True


def fallback(p) -> bool:
    out_dir = config.record_dir(p)
    existing = scenarios.load_records(out_dir)
    if existing and scenarios.setup_problem(existing):
        ui.warn(f"ignoring the existing recordings in {config.rel(out_dir)}: every call in them failed "
                "with the same setup error")
        existing = {}
    if existing:
        dates = sorted({r.get("generated_at", "unknown date") for r in existing.values()})
        ui.warn(f"using existing recordings in {config.rel(out_dir)} (recorded {', '.join(dates)})")
        if any(r.get("source") != "recorded" for r in existing.values()):
            ui.loud(f"THESE ARE BACKUP SAMPLES, NOT real {p.DISPLAY_NAME} responses")
        return True
    samples = sorted(config.backup_recordings_dir(p).glob("*.json"))
    if not samples:
        ui.fail(f"no recordings and no backup samples in {config.rel(config.backup_recordings_dir(p))}")
        return False
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in samples:
        shutil.copy2(f, out_dir / f.name)
    ui.loud(f"USING BACKUP SAMPLE RECORDINGS: these are NOT real {p.DISPLAY_NAME} responses")
    ui.info(f"copied {len(samples)} sample recordings to {config.rel(out_dir)}")
    return True


def show_complexity(p) -> None:
    records = scenarios.load_records(config.record_dir(p))
    ui.say()
    ui.say(ui.bold("  What one call returns:"))
    for name in p.RECORD_SCENARIOS:
        rec = records.get(name)
        if not rec:
            continue
        src = rec.get("source", "")
        ui.say(f"  {ui.bold(name)}" + (ui.dim(f"   [{src}]") if src != "recorded" else ""))
        for step in rec["steps"]:
            body = step.get("body") or {}
            if step.get("kind") == "error":
                err = body.get("error") or {}
                what = f"error {step.get('http_status')} {err.get('type')}"
            else:
                what = f"{body.get('object')} status={body.get('status')}"
            ui.info(f"{step['call']:<15} {ui.SYM['arrow']} {what:<40} "
                    f"{len(body)} top-level field{'s' if len(body) != 1 else ''}, "
                    f"{scenarios.count_fields(body)} total, {scenarios.depth(body)} levels deep")
    ui.say()
    for line in p.COMPLEXITY_NOTE.splitlines():
        ui.say(f"  {line}")
    ui.say()
    first = p.RECORD_SCENARIOS[0] if p.RECORD_SCENARIOS else None
    if first:
        ui.say(ui.bold(f"  Open {config.rel(config.record_dir(p) / f'{first}.json')}"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="don't call the real service")
    args = parser.parse_args(argv)
    config.load_env()
    p = config.load_provider()
    ui.banner(0, f"Record real {p.DISPLAY_NAME} responses")

    recorded = False
    if args.offline:
        ui.warn("--offline: not calling the real service")
    else:
        problem = p.key_problem()
        if problem:
            ui.warn(problem)
        else:
            recorded = record_real(p)
    if not recorded and not fallback(p):
        return 1
    show_complexity(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
