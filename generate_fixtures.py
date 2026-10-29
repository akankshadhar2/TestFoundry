"""Step 1: Claude generates one fixture per scenario, in parallel, extrapolating from the recordings.

    python generate_fixtures.py [--only NAME] [--offline]

Per-scenario fallback to the backup fixture; if Claude is unavailable up front, backups for all.
"""

import argparse
import concurrent.futures
import shutil
import sys
import time

from toolkit import claude, config, scenarios, ui

SYSTEM = ("You are an expert on the {display} API who writes precise, realistic test fixtures. "
          "You reply with a single JSON object and nothing else.")

PROMPT = """\
We are building test fixtures for code that uses {display} through this gateway interface:

{interface}

{notes}

Here are REAL responses recorded from {display} test mode (client secrets removed). Your bodies
must be shaped like these: same fields, same nesting, same style of values.

{recordings}

Write the fixture for this scenario:

name: {name}
description: {description}
steps (executed in order; "$stepN.id" means the id of the resource produced by step N, which for a
card-decline error is error.payment_intent.id):
{steps}

Rules:
- Return exactly one entry per step, in order: {n_steps} step(s).
- Each entry is {{"kind": "success" | "error", "http_status": <int>, "body": <full JSON body>}}.
- Success: http_status 200 and the full object {display} returns (it must have an "id").
- Error: the HTTP status {display} really uses and the exact error body {display} sends
  ({{"error": {{"type": ..., "code": ..., "message": ..., ...}}}}). Use your knowledge of the real API
  for behavior that is not in the recordings: status, error type, code, decline_code, message.
- Make bodies realistic and complete, shaped like the recordings. Keep IDs consistent across steps
  (a refund's payment_intent is the id created in step 0, and so on).

Reply with JSON only: {{"steps": [ ... ]}}
"""


def load_recordings(p):
    recs = scenarios.load_records(config.record_dir(p))
    if recs:
        return recs, config.rel(config.record_dir(p))
    recs = scenarios.load_records(config.backup_recordings_dir(p))
    ui.warn(f"no step-0 recordings found; using backup samples from {config.rel(config.backup_recordings_dir(p))}")
    return recs, config.rel(config.backup_recordings_dir(p))


def build_prompt(p, sc, recordings) -> str:
    rec_text = "\n".join(f"Recording '{name}':\n{scenarios.compact(rec.get('steps'))}"
                         for name, rec in recordings.items())
    steps = "\n".join(f"  {i}. {s['call']}({', '.join(f'{k}={v!r}' for k, v in s['args'].items())})"
                      for i, s in enumerate(sc["steps"]))
    return PROMPT.format(display=p.DISPLAY_NAME, interface=p.INTERFACE_DOC, notes=p.PROMPT_NOTES,
                         recordings=rec_text, name=sc["name"], description=sc.get("description", ""),
                         steps=steps, n_steps=len(sc["steps"]))


def validate(data, sc) -> list:
    problems = []
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list):
        return ['reply must be an object with a "steps" list']
    if len(steps) != len(sc["steps"]):
        problems.append(f"expected {len(sc['steps'])} steps, got {len(steps)}")
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            problems.append(f"step {i} is not an object")
            continue
        kind, status, body = st.get("kind"), st.get("http_status"), st.get("body")
        if kind not in ("success", "error"):
            problems.append(f"step {i}: kind must be 'success' or 'error', got {kind!r}")
        if not isinstance(status, int) or isinstance(status, bool):
            problems.append(f"step {i}: http_status must be an integer")
        if not isinstance(body, dict):
            problems.append(f"step {i}: body must be a JSON object")
            continue
        if kind == "success":
            if status != 200:
                problems.append(f"step {i}: successful calls use http_status 200")
            if not body.get("id"):
                problems.append(f"step {i}: success body must have an 'id'")
        if kind == "error":
            if isinstance(status, int) and status < 400:
                problems.append(f"step {i}: error http_status must be >= 400")
            if not isinstance(body.get("error"), dict) or not body["error"].get("type"):
                problems.append(f"step {i}: error body must have error.type")
    return problems


def generate_one(p, sc, recordings):
    """Returns the fixture record. Raises ClaudeUnavailable or ValueError (with the reason)."""
    prompt = build_prompt(p, sc, recordings)
    system = SYSTEM.format(display=p.DISPLAY_NAME)
    text = claude.ask(prompt, system=system, max_tokens=16000)
    try:
        data = claude.extract_json(text)
        problems = validate(data, sc)
    except ValueError as e:
        problems = [f"reply was not valid JSON ({e})"]
    if problems:
        retry = (prompt + "\n\nYour previous reply had these problems:\n"
                 + "\n".join(f"- {x}" for x in problems) + "\nReturn the corrected JSON only.")
        text = claude.ask(retry, system=system, max_tokens=16000)
        try:
            data = claude.extract_json(text)
        except ValueError as e:
            raise ValueError(f"still not valid JSON after retry ({e})") from None
        problems = validate(data, sc)
        if problems:
            raise ValueError("; ".join(problems[:3]))
    steps = [{"call": spec["call"], "args": spec["args"], "kind": got["kind"],
              "http_status": got["http_status"], "body": got["body"]}
             for spec, got in zip(sc["steps"], data["steps"])]
    return {"scenario": sc["name"], "description": sc.get("description", ""), "source": "ai-generated",
            "model": claude.model(), "generated_at": scenarios.now_iso(), "steps": steps}


def use_backup(p, name, out_dir) -> bool:
    src = config.backup_fixtures_dir(p) / f"{name}.json"
    if not src.is_file():
        ui.fail(f"{name}: no backup fixture at {config.rel(src)}")
        return False
    shutil.copy2(src, out_dir / f"{name}.json")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", metavar="NAME", help="regenerate just this scenario")
    parser.add_argument("--offline", action="store_true", help="don't call Claude; use backup fixtures")
    args = parser.parse_args(argv)
    config.load_env()
    p = config.load_provider()
    ui.banner(1, "Claude generates test fixtures")

    todo = p.SCENARIOS
    if args.only:
        todo = [s for s in p.SCENARIOS if s["name"] == args.only]
        if not todo:
            ui.fail(f"unknown scenario {args.only!r}. Choose from: {', '.join(s['name'] for s in p.SCENARIOS)}")
            return 2
    out_dir = config.fixtures_dir(p)
    out_dir.mkdir(parents=True, exist_ok=True)
    for sc in todo:  # regenerated fixtures make any planted-flaw snapshot stale
        (out_dir / f"{sc['name']}.json.preflaw").unlink(missing_ok=True)

    why = "--offline" if args.offline else claude.problem()
    if why:
        ui.warn(f"Claude unavailable ({why}): using backup fixtures for all {len(todo)} scenarios")
        copied = sum(use_backup(p, sc["name"], out_dir) for sc in todo)
        ui.say()
        ui.ok(f"0 generated by Claude, {copied} from backup  {ui.SYM['arrow']} {config.rel(out_dir)}/")
        return 0 if copied == len(todo) else 1

    recordings, rec_from = load_recordings(p)
    ui.info(f"{len(recordings)} recordings from {rec_from} as examples; "
            f"{len(todo)} scenarios to write, model {claude.model()}")
    ui.info(f"one Claude call per scenario, in parallel{ui.SYM['ellipsis']}")
    ui.say()

    started = time.monotonic()
    generated, backed_up, failed = 0, 0, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(generate_one, p, sc, recordings): sc for sc in todo}
        for fut in concurrent.futures.as_completed(futures):
            sc = futures[fut]
            name = sc["name"]
            try:
                record = fut.result()
            except (claude.ClaudeUnavailable, ValueError) as e:
                if use_backup(p, name, out_dir):
                    backed_up += 1
                    ui.warn(f"{name + '.json':<30} from backup ({e})")
                else:
                    failed += 1
                continue
            except Exception as e:  # never let one scenario crash the step
                if use_backup(p, name, out_dir):
                    backed_up += 1
                    ui.warn(f"{name + '.json':<30} from backup (unexpected {type(e).__name__}: {e})")
                else:
                    failed += 1
                continue
            scenarios.save_json(out_dir / f"{name}.json", record)
            generated += 1
            ui.ok(f"{name + '.json':<30} {ui.dim(scenarios.outcome_line(record, p))}  "
                  f"{ui.dim(f'{time.monotonic() - started:.0f}s')}")

    ui.say()
    ui.ok(f"{generated} generated by Claude, {backed_up} from backup"
          + (f", {failed} FAILED" if failed else "")
          + f"  ({time.monotonic() - started:.0f}s)  {ui.SYM['arrow']} {config.rel(out_dir)}/")
    extrapolated = [s["name"] for s in todo if s["name"] not in p.RECORD_SCENARIOS]
    if generated and extrapolated:  # point at one Claude had to extrapolate (never recorded)
        ui.say(ui.bold(f"  Open {config.rel(out_dir / (extrapolated[0] + '.json'))}"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
