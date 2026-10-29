# AI-generated test doubles, verified against the real service

Claude generates test fixtures and an in-memory fake of Stripe from a few real recordings. Your
tests run in milliseconds against the fake. The same tests then run against real Stripe test mode,
which shows where the AI guessed wrong, and Claude repairs the fake using the real responses as the
source of truth.

The full design is in [`docs/DESIGN.md`](docs/DESIGN.md), and the presenter script is in [`DEMO.md`](DEMO.md).

## Setup

1. **Stripe:** create a free account at <https://dashboard.stripe.com/register>. Switch to
   **test mode**, open *Developers → API keys* and copy the **secret key** (`sk_test_...`).
   Live keys are refused everywhere.
2. **Claude**, either way:
   - **Free with a Claude subscription:** install [Claude Code](https://claude.com/claude-code) and log in
     once (run `claude`). Leave `ANTHROPIC_API_KEY` empty and the scripts call `claude -p`.
   - **Anthropic API (paid, a few cents per run):** create a key at <https://console.anthropic.com>.
3. Install (Python 3.10+):
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
4. `cp .env.example .env` and fill in `STRIPE_TEST_KEY` (and `ANTHROPIC_API_KEY` only if you use the API).
5. Run `python preflight.py`.

Keys are read from the environment or `.env` (gitignored). They are never printed and never sent
to Claude, and `client_secret` values are stripped from everything that goes to Claude.

## Commands

| Step | Command | What it does |
|---|---|---|
| Pre | `python preflight.py [--no-ping]` | Checklist: Python, packages, keys, Stripe and Claude reachable, backups, current state |
| 0 | `python record.py [--offline]` | Records 3 real Stripe calls and prints how complex they are |
| 1 | `python generate_fixtures.py [--only NAME] [--offline]` | Claude writes one fixture per scenario, in parallel |
| 2 | `python generate_fake.py [--keep] [--offline]` | Claude writes `generated/stripe/fake_stripe.py`, which is validated and self-corrected |
| opt | `python inject_flaw.py [--undo]` | Plants a realistic mistake so step 4 is guaranteed to show red |
| 3 | `python -m pytest --backend=fake` | All tests against the fake, with no network |
| 4 | `python -m pytest --backend=real` | The same tests against Stripe test mode; real responses are saved |
| 5 | `python repair_fake.py` | Corrects the fixtures from the real responses, then Claude repairs the fake |
| 6 | rerun steps 3 and 4 | Both green |
| | `python -m pytest --backend=replay` | Contract tests against the real responses saved in rehearsal (no network) |
| | `python reset.py [--from-backup]` | Wipes `generated/`. `--from-backup` makes it instantly demo-ready |
| | `python save_backup.py [--yes]` | After a good rehearsal, saves the generated output as the new backup |
| | `python tools/build_backup.py` | Rebuilds backup fixtures and sample recordings from the hand-written backup fake |

## How the tests stay honest

- `test_contract.py` (generic) runs every scenario against the backend and compares a summary of
  each step with the fixture. The summary for a success is ok/object/status/amount/currency, and for
  an error it is http_status/type/code/decline_code.
- The fixtures are Claude's *expectations*, and the fake is built from them, so the fake passes. The
  real run is the source of truth, and it exposes every wrong expectation as an
  "AI fixture expected X / real Stripe did Y" diff.
- `test_payments.py` asserts only behavior that is certain (see DESIGN.md 7.3). No guessed Stripe
  error codes are hard-coded in any test.

## How foolproofing works

Every step that depends on Claude or the network has a fallback. Each script prints exactly what
happened and exits 0 whenever the demo can continue.

| Step | If it fails | Fallback |
|---|---|---|
| preflight | key missing or service unreachable | Reported as a warning with its fix. The status is READY with fallbacks. Exits 1 only when the backups or pytest are broken |
| 0 record | no key, live key, `--offline`, network/auth error | Reuses existing recordings (printing their date), else copies the backup **samples** with a loud warning |
| 1 fixtures | Claude unavailable up front | Backup fixtures for every scenario |
| 1 fixtures | one call fails or returns invalid JSON twice | That scenario falls back to its backup fixture. Validation problems first get one retry with the problem list |
| 2 fake | AI fake fails validation | Up to 2 self-fix rounds. The checks are: parse, import in a subprocess, class, exact signatures, stdlib-only, no I/O, no per-scenario hard-coding, then the fake suite |
| 2 fake | still failing, or Claude unavailable | Backup fake if it passes the current fixtures, otherwise backup fixtures **and** backup fake together (a consistent set). `--keep` keeps the AI version |
| 4 real | no or live key, 401/403, 429, ≥500, connection error | Clean `pytest.exit` with a "use `--backend=replay`" tip. The broken record is not saved |
| 5 repair | Claude unavailable or still failing | Strips the planted flaw block mechanically and retests, then tries the backup fake, then restores the previous fake and prints the options |
| any | anything weird | `python reset.py --from-backup`, then continue from step 3 |

Claude refusals (`stop_reason: refusal`), API errors and network errors all become
`ClaudeUnavailable`, which every script catches.

## Swapping providers

Stripe-specific code lives only in `providers/stripe_provider.py` (plus `app.py`, `test_payments.py`
and the backup fake). To demo a different service:

1. Copy `providers/_template_provider.py` to `providers/<name>_provider.py` and implement
   everything in DESIGN.md section 5. That covers scenarios, prompt text, `summarize`,
   `resource_id`, `transport_problem`, `key_problem` (refuse live keys!), the real gateway, and the
   flaw. Also set `GATEWAY_METHODS` to your gateway's method signatures.
2. Replace `app.py` and `test_payments.py`, or add `test_<name>.py` with a module-level
   `pytest.skip(..., allow_module_level=True)` when the provider doesn't match.
3. Set `DEMO_PROVIDER=<name>` in `.env`.
4. Hand-write `backup/<name>/fake_<name>.py`, then run `python tools/build_backup.py` to generate
   the backup fixtures and sample recordings from it.

No changes are needed in `toolkit/`, `conftest.py`, `test_contract.py` or any script.

## Refreshing the backups

The committed backup set (`backup/stripe/`) was produced from the hand-written
`backup/stripe/fake_stripe.py`. Its recordings are labeled `sample (not a real recording)`. Where the
real Stripe error code was uncertain, the backup fake uses a best guess: `charge_not_refundable`
for refunding a declined payment, `amount_too_large` for over-refunding, and 404 `resource_missing`
for unknown ids. The real run corrects these, which is itself a legitimate demo moment.

After a good rehearsal with real keys, run `python save_backup.py`. This freezes the real
recordings, Claude's fixtures and Claude's fake as the new backup.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No generated fake. Run: python generate_fake.py` | Run it, or `python reset.py --from-backup` |
| `No real-run recording` on `--backend=replay` | Run `python -m pytest --backend=real` once during rehearsal |
| `STRIPE_TEST_KEY is a LIVE key` | Use the **test mode** secret key (`sk_test_...`) |
| Claude ping fails in preflight | Check `ANTHROPIC_API_KEY` and `CLAUDE_MODEL`. The demo still runs on backups |
| Claude is slow on stage | Set `CLAUDE_EFFORT=low` in `.env`, or use the `--offline` flags |
| Step 3 fails after a planted flaw and a regeneration | `python inject_flaw.py --undo`, or `python reset.py --from-backup` |
| Step 4 shows no failures | `python inject_flaw.py`, then rerun steps 3 and 4 |

## Notes on the implementation (choices where the design left room)

- **Layout:** the project root is this repository root, not an `ai-test-doubles-demo/` subfolder.
  `DESIGN.md` lives in `docs/`.
- **`toolkit/fakes.py`** (not in the design's layout) holds the shared fake helpers: loading,
  structural validation, the self-fix loop, running the fake suite in a subprocess, and the
  flaw-marker helpers. Claude-written code is only ever imported in a subprocess with a timeout.
- **Two optional provider attributes**, `GATEWAY_METHODS` (the exact method signatures to check)
  and `FAKE_REQUIREMENTS` (service-specific bullets for the fake prompt), keep the generic scripts
  free of Stripe text. If they are missing, the DESIGN.md 4.1 payments interface is used.
- **`real_run.json` format:** `{"provider", "recorded_at", "records": {scenario: record}}`. Each
  record uses the common record format with `source: "real-run"`. New real runs are merged in.
- **Extra repair fallback:** before restoring the previous fake, `repair_fake.py` also tries the
  backup fake against the corrected fixtures.
- **Crashing fakes:** if a fake raises something other than `GatewayError`, the step is recorded
  as an error of type `python_exception`. A buggy fake therefore shows up as a readable diff, not a
  traceback.
- **Regenerating after a planted flaw:** `generate_fake.py` restores the `.preflaw` fixture first,
  so Claude never learns the planted mistake. `generate_fixtures.py` deletes stale `.preflaw`
  files for the scenarios it rewrites.
- **Exit codes:** configuration problems inside pytest (no key, live key, no replay recording,
  transport trouble) use `pytest.exit(..., returncode=4)`. `inject_flaw.py` and `repair_fake.py`
  exit 1 only when there is nothing they can do, and they print what to run next.
- **Claude Code backend:** `CLAUDE_BACKEND` (auto|api|cli). `auto` uses the API when
  `ANTHROPIC_API_KEY` is set, otherwise `claude -p` (headless Claude Code, with no tools and no
  settings, run in an empty temp folder). Its environment has the API key and every Stripe key
  removed, so it bills the subscription and never sees a service key. At most 3 run in parallel.
  Text still streams live. This uses the subscription's usage limits instead of API credit.
- **Model:** `CLAUDE_MODEL`, default `claude-sonnet-5-5`. The optional `CLAUDE_EFFORT` is sent
  only when set, so an older model never sees an unknown field.
- **`pytest.ini`** also skips `tools`, `docs` and virtualenv folders during collection.
- `recordings/` is persistent and not gitignored (per the design). It can contain **test-mode**
  `client_secret` values, which are never sent to Claude.
