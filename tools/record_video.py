"""Record the demo as a replayable terminal "video" (an HTML page, no ffmpeg needed).

    python tools/record_video.py            # full demo if keys are set, offline demo otherwise
    python tools/record_video.py --offline  # force the no-keys version

Each step runs in a pseudo-terminal so colors and timing come through. The output is
docs/demo_video.html. Open it in a browser and press play, or screen-record it for an .mp4.
Keys are never printed by the scripts, so they never end up in the recording.
"""

import json
import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from toolkit import config  # noqa: E402

PY = "python"  # what the viewer sees typed; the real interpreter is sys.executable

FULL = [
    ("Pre-demo checklist: Python, packages, keys, Stripe and Claude reachable.", "preflight.py", []),
    ("Start clean.", "reset.py", []),
    ("Step 0. Record three real Stripe test-mode calls. Real payment objects are big.", "record.py", []),
    ("Step 1. Claude writes the expected result for all nine scenarios, six of them guessed.", "generate_fixtures.py", []),
    ("Step 2. Claude writes a fake Stripe. It is checked automatically and fixes itself if needed.", "generate_fake.py", []),
    ("Optional. Plant a realistic mistake so the real run is sure to catch something.", "inject_flaw.py", []),
    ("Step 3. All tests against the fake: instant, no network.", "-m pytest", ["--backend=fake"]),
    ("Step 4. The same tests against real Stripe. Red shows where the AI guessed wrong.", "-m pytest", ["--backend=real"]),
    ("Step 5. Real Stripe is the source of truth: fixtures are corrected and Claude repairs the fake.", "repair_fake.py", []),
    ("Step 6. Fake again: still instant.", "-m pytest", ["--backend=fake"]),
    ("Step 6. Real again: everything green. Fast and correct.", "-m pytest", ["--backend=real"]),
]

OFFLINE = [
    ("Pre-demo checklist. No keys are set, so it reports which fallbacks will be used.", "preflight.py", ["--no-ping"]),
    ("Start clean.", "reset.py", []),
    ("Step 0 (offline). Without a Stripe key, the saved sample recordings are used.", "record.py", ["--offline"]),
    ("Step 1 (offline). Without Claude, the backup fixtures are used for all nine scenarios.", "generate_fixtures.py", ["--offline"]),
    ("Step 2 (offline). Without Claude, the backup fake is checked and installed.", "generate_fake.py", ["--offline"]),
    ("Step 3. All tests against the fake: instant, no network.", "-m pytest", ["--backend=fake"]),
    ("Optional. Plant a realistic mistake: the fake now thinks a declined payment can be refunded.", "inject_flaw.py", []),
    ("Step 3 again. The fake still passes, because it was built from the same wrong expectation.", "-m pytest", ["--backend=fake"]),
    ("Step 4 needs a Stripe test key. Without one, it stops cleanly and says what to do.", "-m pytest", ["--backend=real"]),
    ("Undo the planted mistake.", "inject_flaw.py", ["--undo"]),
]


def keys_present() -> bool:
    config.load_env()
    from toolkit import claude
    stripe_key = os.environ.get("STRIPE_TEST_KEY", "")
    return stripe_key.startswith(("sk_test_", "rk_test_")) and claude.problem() is None


def run_in_pty(args: list[str], timeout: float = 900) -> tuple[list[list], int]:
    """Run a command under a pty. Returns ([[seconds_since_start, text], ...], exit code)."""
    master, slave = pty.openpty()
    env = dict(os.environ, TERM="xterm-256color", COLUMNS="110", LINES="40", PYTHONUNBUFFERED="1")
    env.pop("NO_COLOR", None)
    proc = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    events, start, buf = [], time.monotonic(), b""
    while True:
        if time.monotonic() - start > timeout:
            proc.kill()
            events.append([round(time.monotonic() - start, 3), "\r\n[recording stopped: step took too long]\r\n"])
            break
        ready, _, _ = select.select([master], [], [], 0.1)
        if ready:
            try:
                data = os.read(master, 4096)
            except OSError:
                data = b""
            if not data:
                break
            buf += data
            try:
                text, buf = buf.decode("utf-8"), b""
            except UnicodeDecodeError:
                continue  # wait for the rest of a multi-byte character
            events.append([round(time.monotonic() - start, 3), text])
        elif proc.poll() is not None:
            break
    os.close(master)
    return events, proc.wait()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    full = "--offline" not in argv and keys_present()
    plan = FULL if full else OFFLINE
    print(f"Recording the {'full' if full else 'offline'} demo ({len(plan)} steps).", flush=True)
    if not full and "--offline" not in argv:
        print("  (Needs STRIPE_TEST_KEY in .env plus Claude Code or ANTHROPIC_API_KEY; recording the offline version.)")

    steps = []
    for i, (caption, script, extra) in enumerate(plan, 1):
        shown = f"{PY} {script} {' '.join(extra)}".strip()
        print(f"  [{i}/{len(plan)}] {shown}", flush=True)
        real = [sys.executable, *script.split(), *extra]
        events, code = run_in_pty(real)
        steps.append({"caption": caption, "command": shown, "events": events, "exit": code})

    cast = {"mode": "full" if full else "offline", "recorded": time.strftime("%Y-%m-%d %H:%M"), "steps": steps}
    template = (ROOT / "tools" / "video_player.html").read_text(encoding="utf-8")
    payload = json.dumps(cast, ensure_ascii=False).replace("</", "<\\/")
    out = ROOT / "docs" / "demo_video.html"
    out.write_text(template.replace("/*CAST*/null", payload), encoding="utf-8")
    print(f"Saved {out.relative_to(ROOT)}. Open it in a browser and press play.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
