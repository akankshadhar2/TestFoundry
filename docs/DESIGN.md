# Design Doc: AI-Generated Test Fixtures & Test Doubles, Verified Against the Real Service

## 1. Purpose

A live, ~10-minute demo showing:

1. **AI generates test fixtures** for a complex external resource (Stripe), extrapolating from a few real recordings.
2. **AI generates a test double** (an in-memory fake Stripe) from those fixtures.
3. Tests run **fast against the fake**.
4. The **same tests run against real Stripe** (test mode). Divergences show where the AI guessed wrong.
5. **AI repairs the double** using real Stripe as the source of truth. Both backends then pass.

Story for the audience: *AI makes doubles fast and cheap; integration against the real resource keeps them honest.*

Non-negotiable qualities:
- **Foolproof.** Every step that depends on the network or on Claude has a fallback, so the demo never dead-ends on stage.
- **Swappable.** Stripe-specific code lives in one "provider" module. Swapping Stripe for another service means writing a new provider (plus app code and app tests), not touching the pipeline.
- **Simple to run.** One command per step, clear colored output, no prompts during the demo.

## 2. Demo flow (what the presenter runs)

| Step | Command | What the audience sees |
|---|---|---|
| Pre | `python preflight.py` | Green/red checklist: Python, packages, keys, Stripe reachable, Claude reachable, backups present |
| 0 | `python record.py` | A few real Stripe calls; saved JSON; complexity stats (e.g. "48 top-level fields, 4 levels deep") and the PaymentIntent state machine |
| 1 | `python generate_fixtures.py` | Claude generates one fixture per scenario in parallel; each file appears with a one-line outcome summary |
| 2 | `python generate_fake.py` | Claude's code streams into the terminal; the fake is validated and self-corrected against the fixtures; `fake_stripe.py` written |
| (opt) | `python inject_flaw.py` | Plants one realistic mistake (fake believes a declined payment can be refunded), so step 4 is guaranteed to show a failure |
| 3 | `python -m pytest --backend=fake` | All green, in a fraction of a second, no network |
| 4 | `python -m pytest --backend=real` | Same tests against Stripe test mode; one or more red with a readable "AI expected X / real Stripe did Y" diff |
| 5 | `python repair_fake.py` | Fixtures corrected from real responses; Claude patches the fake (streamed); tests rerun green |
| 6 | rerun steps 3 and 4 | Both green |

## 3. Project layout

```
ai-test-doubles-demo/
  README.md               # setup, commands, how foolproofing works, how to swap providers
  DEMO.md                 # presenter script: commands, talking points, timings, recovery table
  requirements.txt        # stripe, anthropic, pytest
  .env.example            # STRIPE_TEST_KEY=, ANTHROPIC_API_KEY=, optional CLAUDE_MODEL=, DEMO_PROVIDER=
  .gitignore              # .env, generated/, __pycache__/, *.prev, *.preflaw
  pytest.ini              # pythonpath = . ; norecursedirs = backup generated recordings providers toolkit ; addopts = -p no:cacheprovider -v

  app.py                  # code under test: charge() and refund()
  conftest.py             # --backend=fake|real|replay, gateway fixtures, saves real-run results
  test_contract.py        # GENERIC: replays every scenario against the backend, compares to fixtures
  test_payments.py        # Stripe/app-specific tests of app.py (skips if provider != stripe)

  record.py               # step 0
  generate_fixtures.py    # step 1
  generate_fake.py        # step 2
  inject_flaw.py          # optional: plant/undo a known flaw
  repair_fake.py          # step 5
  preflight.py            # pre-demo checks
  reset.py                # wipe generated/; --from-backup makes it instantly demo-ready
  save_backup.py          # after a good rehearsal, freeze generated output as the new backup

  toolkit/
    __init__.py
    config.py             # ROOT, .env loader, provider loading, paths, model name
    gateway.py            # GatewayError
    scenarios.py          # run scenarios, summarize, compare, format diffs, JSON helpers
    claude.py             # Anthropic calls (streaming + non-streaming), JSON/code extraction
    ui.py                 # colored banners and status lines (respects NO_COLOR, UTF-8 safe)

  providers/
    __init__.py
    stripe_provider.py    # ALL Stripe-specific knowledge
    _template_provider.py # skeleton with TODOs for adding another service

  backup/stripe/          # committed fallbacks, internally consistent
    recordings/*.json     # sample recordings (labeled "sample", replaced after rehearsal)
    fixtures/*.json       # one per scenario
    fake_stripe.py        # hand-written reference fake that passes backup fixtures + app tests

  recordings/stripe/      # persistent; NOT wiped by reset.py
    record/*.json         # step-0 real recordings
    real_run.json         # latest real-backend test results (used by repair and replay)

  generated/stripe/       # wiped by reset.py
    fixtures/*.json
    fake_stripe.py
```

## 4. Core abstractions

### 4.1 Gateway interface (the "port" the app talks to)

Both the real client and the fake implement exactly:

```python
create_payment(self, amount: int, currency: str, payment_method: str) -> dict
    # creates AND confirms a PaymentIntent; returns PaymentIntent JSON as a plain dict
refund_payment(self, payment_id: str, amount: int | None = None) -> dict
    # refunds a PaymentIntent; amount=None means full refund; returns Refund JSON
```

Parameter names are part of the contract (scenarios call methods by keyword). Validation must check signatures.

### 4.2 GatewayError (`toolkit/gateway.py`)

```python
class GatewayError(Exception):
    def __init__(self, http_status: int, body: dict): ...
    # body is the exact JSON error body the service sends, e.g. {"error": {"type", "code", "decline_code", "message", "payment_intent", ...}}
```

Only two fields. Everything else is derived by the provider. This keeps the fake's job simple: build the same body Stripe would send and raise it.

### 4.3 Scenario format (defined in the provider)

```python
{"name": "refund_declined_payment",
 "description": "Try to refund a payment whose card was declined.",
 "steps": [
   {"call": "create_payment", "args": {"amount": 2000, "currency": "usd", "payment_method": "pm_card_chargeDeclined"}},
   {"call": "refund_payment", "args": {"payment_id": "$step0.id"}}]}
```

`"$stepN.id"` resolves to the resource id produced by step N. For an error step, the provider extracts it from the error body (for Stripe card declines: `error.payment_intent.id`).

### 4.4 Record format (used for recordings, fixtures, and real-run results alike)

```json
{"scenario": "declined_card",
 "description": "...",
 "source": "recorded | ai-generated | real-run | backup | sample",
 "model": "claude-...",            // when ai-generated
 "generated_at": "ISO timestamp",
 "steps": [
   {"call": "create_payment", "args": {...unresolved args...},
    "kind": "success" | "error", "http_status": 402, "body": {...full JSON...}}]}
```

One format everywhere means repair can copy a real-run record straight over a wrong fixture.

### 4.5 Summaries (what is actually compared)

Full bodies are not compared (IDs, timestamps, secrets differ). The provider reduces each step to a summary:

- success: `{"ok": True, "object", "status", "amount", "currency"}`
- error: `{"ok": False, "http_status", "error_type", "error_code", "decline_code"}`

Comparison = summaries equal, step by step. Failure messages show per-field differences in plain language:

```
refund_declined_payment: 1 step differs
  step 1 · refund_payment
    AI fixture expected   success  object=refund status=succeeded amount=2000 currency=usd
    real Stripe did       error    http_status=400 error_type=invalid_request_error error_code=...
```

## 5. Stripe provider (`providers/stripe_provider.py`)

Must expose (this is also the contract for any future provider):

| Name | Purpose |
|---|---|
| `NAME`, `DISPLAY_NAME`, `FAKE_CLASS` | `"stripe"`, `"Stripe"`, `"FakeStripe"` |
| `ENV_KEY` | `"STRIPE_TEST_KEY"` |
| `SCENARIOS` | list of scenario dicts (below) |
| `RECORD_SCENARIOS` | subset recorded in step 0 |
| `INTERFACE_DOC` | text given to Claude describing the gateway methods and error contract |
| `PROMPT_NOTES` | domain notes for Claude (test card tokens, amounts in cents, lifecycle, ID prefixes) — general knowledge only, never the expected error codes |
| `COMPLEXITY_NOTE` | talking point printed by record.py (PaymentIntent state machine) |
| `summarize(kind, http_status, body)` | → summary dict (4.5) |
| `resource_id(kind, body)` | → id for `$stepN.id` |
| `transport_problem(record)` | → message if a real run hit network/auth trouble (401/403, ≥500, `api_connection_error`), else None |
| `key_problem()` | → message if key missing, a LIVE key (`_live_` anywhere → refuse), or not `sk_test_`/`rk_test_` |
| `make_real_gateway()` | → `RealStripe` instance; raises with clear message if `key_problem()` |
| `check_real_access()` | cheap call (e.g. `stripe.Balance.retrieve()`) for preflight |
| `FLAW_DESCRIPTION`, `FLAW_CODE`, `apply_flaw(fixtures_dir)`, `undo_flaw(fixtures_dir)` | the plantable flaw (section 9) |

### 5.1 Scenarios

| name | steps | recorded in step 0? |
|---|---|---|
| `successful_charge` | create 2000 usd `pm_card_visa` | yes |
| `declined_card` | create 2000 usd `pm_card_chargeDeclined` | yes |
| `full_refund` | create visa; refund `$step0.id` | yes |
| `insufficient_funds` | create 2000 usd `pm_card_chargeDeclinedInsufficientFunds` | no |
| `partial_refund` | create visa 2000; refund 500 | no |
| `refund_declined_payment` | create declined; refund `$step0.id` | no |
| `double_refund` | create visa; refund; refund again | no |
| `over_refund` | create visa 2000; refund 5000 | no |
| `below_minimum_amount` | create 10 usd visa | no |

Only 3 are recorded, so Claude must **extrapolate** 6. That is where real divergences naturally appear (exact error codes, HTTP statuses), which makes the demo honest.

### 5.2 RealStripe

- `stripe.api_key = key`.
- `create_payment` → `stripe.PaymentIntent.create(amount=, currency=, payment_method=, payment_method_types=["card"], confirm=True)` (card-only avoids the `return_url` requirement).
- `refund_payment` → `stripe.Refund.create(payment_intent=payment_id, amount=amount)` (omit `amount` when None).
- Convert results to plain dicts robustly across stripe-python versions: try `json.loads(str(obj))` first, then `to_dict_recursive()`, then `to_dict()`.
- Catch the stripe base error class (`getattr(stripe, "StripeError", None) or stripe.error.StripeError`) and raise `GatewayError(e.http_status or 503, e.json_body or {"error": {"type": "api_connection_error", "message": str(e)}})`.

**Important:** do not hard-code what you *believe* Stripe's error codes are into tests. The contract tests compare against fixtures, and the real run decides the truth. Hand-written app tests (6) only assert things that are certain.

## 6. The code under test (`app.py`)

```python
@dataclass ChargeResult: ok, payment_id, status, decline_code=None, message=""
@dataclass RefundResult: ok, refund_id, status, amount, message=""
class PaymentError(Exception)

charge(gateway, amount_cents: int, currency: str, payment_method: str) -> ChargeResult
  - ValueError if amount_cents is not a positive int (no gateway call)
  - card_error → ChargeResult(ok=False, decline_code=..., message=friendly text from own map:
      insufficient_funds → "Your card has insufficient funds.", default → "Your card was declined.")
  - other GatewayError → PaymentError
refund(gateway, payment_id, amount_cents=None) -> RefundResult
  - GatewayError → RefundResult(ok=False, message=error message)
```

## 7. Tests

### 7.1 `conftest.py`

- `--backend` option: `fake` (default), `real`, `replay`.
- Fake: load `generated/<provider>/fake_<provider>.py` via importlib (session-cached). Missing file → `pytest.fail("No generated fake. Run: python generate_fake.py", pytrace=False)`.
- Real: `key_problem()` → `pytest.exit(...)` with a clear message. One shared real gateway per session.
- Replay: loads `recordings/<provider>/real_run.json`; missing → `pytest.exit("No real-run recording. Run --backend=real once during rehearsal.")`. App tests skip on replay.
- Fresh fake instance per test.
- Real-backend contract results collected in a dict; `pytest_sessionfinish` merges them into `real_run.json` (with `recorded_at`).
- `pytest_report_header`: shows backend + provider.
- `pytest_terminal_summary`: fake → "⚡ N tests in X s, no network"; real with failures → "Real responses saved. Next: python repair_fake.py".

### 7.2 `test_contract.py` (generic, provider-agnostic)

Parametrized over scenario names (ids = names). For each:
1. Load fixture; missing → fail with "Run generate_fixtures.py".
2. Get actual record: replay → from real_run.json (skip if absent); else `run_scenario(gateway, scenario, provider)`.
3. Real backend: if `transport_problem(record)` → `pytest.exit(msg + " Tip: use --backend=replay")` and **do not save** that record.
4. Compare summaries; on mismatch `pytest.fail(formatted_diff, pytrace=False)`. Labels: fake → "fixture expects / fake did"; real/replay → "AI fixture expected / real Stripe did".

Why this design is honest: fixtures are AI expectations, the fake is built from fixtures, so the fake passes; the real run reveals wherever the AI's expectations were wrong.

### 7.3 `test_payments.py` (Stripe app tests, run on fake and real)

Module-level skip if provider != stripe. Only certain assertions:
- visa charge → ok, status "succeeded", id starts with "pi_"
- `pm_card_chargeDeclined` → not ok, decline_code "generic_decline", message "Your card was declined."
- insufficient funds → decline_code "insufficient_funds", "insufficient funds" in message
- charge then full refund → ok
- refund twice → second not ok
- amount 0 → ValueError

## 8. Scripts (behavior and fallbacks)

All scripts: banner with step number/title, `ui.ok/warn/fail` lines, never interactive prompts during demo steps, exit 0 whenever the demo can continue.

### `toolkit/claude.py`
- `ClaudeUnavailable` raised for: anthropic not installed, `ANTHROPIC_API_KEY` missing, any `anthropic.APIError`.
- Client: `anthropic.Anthropic(max_retries=4, timeout=180)`.
- Model: `CLAUDE_MODEL` env, default a current Sonnet model id (e.g. `claude-sonnet-5-5`); preflight verifies it works.
- `ask(prompt, system, max_tokens, stream=False)`; streaming prints dimmed text live and returns full text.
- `extract_json(text)` (strip fences, first `{` to last `}`), `extract_python(text)` (largest ```python block, else whole text).

### `preflight.py`
Checks and prints fixes for: Python ≥ 3.10; imports of stripe/anthropic/pytest; Anthropic key present + tiny ping with the configured model (`--no-ping` to skip); Stripe key valid (never a live key) + `check_real_access()`; backup files present; current state (fixtures count, fake exists, flaw planted?, real_run.json present → replay available). Ends with READY or a list of problems. Never prints key values.

### `record.py` (step 0)
- With a valid key: run `RECORD_SCENARIOS` against real Stripe, write to `recordings/<p>/record/`. If `transport_problem` → fallback.
- Fallback (no key, `--offline`, network error): use existing recordings if present (print their date), else copy backup sample recordings with a loud warning.
- Print complexity per step: object type, status, top-level field count, total field count, nesting depth; then `COMPLEXITY_NOTE`; then "Open recordings/stripe/record/successful_charge.json".

### `generate_fixtures.py` (step 1)
- Inputs: all recordings (fallback to backup samples), `INTERFACE_DOC`, `PROMPT_NOTES`, each scenario spec.
- **One Claude call per scenario, in parallel** (ThreadPoolExecutor, ~6 workers) so the audience sees files appear and total time stays ~1 min.
- Claude returns JSON only: `{"steps": [{"kind", "http_status", "body"}, ...]}` one per step. Prompt rules: bodies realistic and shaped like the recordings; IDs consistent across steps; use real-API knowledge for behavior not recorded (status, error type, code, decline_code, message); success status 200.
- Validate: step count matches, kind valid, http_status int, success body has `id`, error body has `error.type`. On problems, retry once with the problem list.
- Merge call/args from the spec; write with `source: ai-generated`, model, timestamp.
- Per-scenario fallback to backup fixture with a warning. If Claude is unavailable up front, use backups for all and say so.
- Flags: `--only NAME`, `--offline`.
- Summary: "N generated by Claude, M from backup".

### `generate_fake.py` (step 2)
- Prompt includes: `INTERFACE_DOC`, `PROMPT_NOTES`, `app.py` source, all fixtures (compact JSON).
- Requirements given to Claude: one self-contained Python 3.10 module; stdlib only; no network or file I/O; `from toolkit.gateway import GatewayError`; class `FakeStripe` with exact signatures; in-memory state per instance; model the real state machine (payments, charges, refunds, remaining refundable amount); reproduce every fixture's summary fields and realistic full bodies; card-decline error bodies include the PaymentIntent under `payment_intent`; deterministic IDs with real prefixes (`pi_`, `ch_`, `re_`, `pm_`); implement behavior generally (map test card tokens to outcomes), never look up fixtures or hard-code scenario names; return one ```python block starting with a docstring.
- Stream the code to the terminal.
- Validation loop (up to 2 self-fix rounds): ast-parse → import → class exists → signatures match → write file (saving previous as `.prev`) → run `python -m pytest -q --backend=fake` in a subprocess. On failure, send failures (tail of output) back to Claude for a full corrected file. Print "AI self-check found N problems, fixing…".
- Fallback: if still failing or Claude unavailable → if backup fake passes current fixtures, use it; otherwise copy backup fixtures **and** backup fake together (a consistent set). Always say exactly what happened. `--keep` keeps the AI version even if failing. `--offline` goes straight to backup.
- Finish: lines of code, path, "Open generated/stripe/fake_stripe.py".

### `inject_flaw.py` (optional before step 3)
- Appends `FLAW_CODE` (between `# >>> DEMO FLAW >>>` / `# <<< DEMO FLAW <<<` markers) to the generated fake. Idempotent.
- `apply_flaw` saves `refund_declined_payment.json.preflaw`, then rewrites that fixture's refund step to a successful refund matching what the flaw returns, so step 3 stays green.
- `--undo` removes the block and restores the `.preflaw` fixture.
- Prints the planted mistake in one sentence for the presenter to say aloud.

### `repair_fake.py` (step 5)
1. Load `real_run.json` (missing → instruct to run `--backend=real` or `--backend=replay`).
2. Compare each fixture with its real record; print each mismatch as "AI expected X / real did Y".
3. If none: run fake tests; if green → "Nothing to repair", exit 0.
4. **Deterministically** overwrite wrong fixtures with the real records (`source: real-run`), delete stale `.preflaw` files. Print "fixture X corrected from real Stripe".
5. Ask Claude (streamed) for a full corrected fake, given: current fake source, the mismatch report, and the real bodies for mismatched scenarios. Tell it to remove anything that contradicts real behavior, including any injected DEMO FLAW block.
6. Same validation loop as generate_fake (structural + fake test run, up to 2 self-fix rounds). Also reject output that still contains the flaw marker.
7. Fallbacks: if Claude fails and the fake contains the flaw block, strip the block mechanically and retest (announce it). If still failing, restore the previous fake and print next options (rerun repair, or `generate_fake.py` from the corrected fixtures).
8. End: "Now rerun: python -m pytest --backend=fake  and  --backend=real".

### `reset.py`
Deletes `generated/<provider>/`. `--from-backup` copies backup fixtures + fake into place (instantly demo-ready). Never deletes `recordings/`.

### `save_backup.py`
After a good rehearsal: copy generated fixtures + fake + step-0 recordings into `backup/<provider>/`. Refuse if the fake contains the flaw marker or fails the fake suite. Asks y/N unless `--yes`.

## 9. The plantable flaw

`FLAW_CODE` wraps the fake class generically (doesn't depend on Claude's internals):
- wraps `create_payment`: on a `card_error` GatewayError, remember `(payment_intent id → amount, currency)` in a module-level dict keyed by `id(self)`, then re-raise;
- wraps `refund_payment`: if `payment_id` is a remembered declined payment, return a realistic successful Refund dict (`id` "re_demoflaw…", object "refund", amount, currency, status "succeeded", payment_intent); otherwise call the original.

Real Stripe rejects that refund, so step 4 shows a clear red test. Say on stage: "To make sure we see it, I've planted a realistic mistake: the fake thinks a declined payment can be refunded."

## 10. Backup set (`backup/stripe/`)

- Write `fake_stripe.py` by hand: realistic full PaymentIntent/Refund/PaymentMethod/error bodies (all common fields, nulls included), test cards (`pm_card_visa`, `pm_card_mastercard`, `pm_card_chargeDeclined`, `pm_card_chargeDeclinedInsufficientFunds`), per-currency minimums (usd 50), zero-decimal handling, refund tracking, unknown ids → 404 `resource_missing`.
- **Generate backup fixtures by running every scenario through the backup fake** (guarantees consistency). Backup recordings = those fixtures for `RECORD_SCENARIOS`, labeled `source: sample (not a real recording)`.
- Where the real error code is uncertain, use a best guess. That is fine: the real run corrects it and is a legitimate demo moment. README says to refresh backups with `save_backup.py` after rehearsal.

## 11. Swapping providers

To demo a different service:
1. Copy `providers/_template_provider.py` → `providers/<name>_provider.py`; implement everything in the section 5 table.
2. Replace `app.py` and `test_payments.py` (or add `test_<name>.py` with a module-level provider skip).
3. `DEMO_PROVIDER=<name>` in `.env`.
4. Write a backup fake, generate backup fixtures from it.

No changes to toolkit/, conftest.py, test_contract.py, or any script. README documents this with the checklist.

## 12. Safety

- Only test keys. Anything containing `_live_` is refused everywhere.
- Keys come from environment or `.env` (gitignored); never printed, never sent to Claude.
- Recordings may contain test-mode `client_secret` values; strip `client_secret` from anything sent to Claude.

## 13. Docs to write

**README.md:** what it is (3 sentences); setup (create Stripe account, copy test secret key, Anthropic key, `pip install -r requirements.txt`, copy `.env.example` → `.env`); command list; how foolproofing works (fallback table); swapping providers; troubleshooting.

**DEMO.md:**
- *Day before (rehearsal):* preflight → full run 0–6 with real keys (this creates `real_run.json` for replay) → `save_backup.py` → `reset.py`.
- *Demo day, 10 min before:* `preflight.py`; `reset.py` (or `reset.py --from-backup` if the network is bad).
- *Script:* each step with the command, what to point at on screen, 1–2 sentences to say, expected duration.
- *Recovery table:*

| Problem on stage | Do this |
|---|---|
| Claude API down/slow | Scripts auto-fallback to backup; say "here's the version it generated in rehearsal" |
| Wi-Fi dies before step 4 | `python -m pytest --backend=replay` (real responses from rehearsal) |
| Step 4 shows no failures | Run `inject_flaw.py`, rerun steps 3–4 |
| Fake fails in step 3 | `python repair_fake.py` or `reset.py --from-backup` |
| Anything weird | `reset.py --from-backup`, continue from step 3 |

## 14. Acceptance criteria

With **no keys and no network**:
- `reset.py --from-backup` then `pytest --backend=fake` → all pass.
- `generate_fixtures.py` and `generate_fake.py` fall back to backups cleanly, exit 0, and step 3 passes.
- `inject_flaw.py` → fake suite still passes; `--undo` restores exactly.
- `--backend=real` exits with a clear message (not a traceback); `--backend=replay` without a recording exits with a clear message.
- `preflight.py` reports each missing item with its fix.
- Repair flow works end to end when tested with a mocked Claude (`ask` monkeypatched) and a synthetic `real_run.json` that differs from fixtures: fixtures get corrected, fake replaced, suite green.
- RealStripe conversion and error mapping work on locally constructed stripe objects (`stripe.PaymentIntent.construct_from(...)`, `stripe.CardError(..., json_body=...)`).
- A live key is refused.

With **real keys**: full flow 0–6 runs, step 4 shows at least one divergence (natural or planted), step 5 makes both backends green.
