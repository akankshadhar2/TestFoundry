Build the project described in DESIGN.md (in this folder). Read it fully before writing any code.

## What it is
A live demo project: AI (Claude, via the Anthropic API) generates test fixtures and an in-memory test double for Stripe, tests run fast against the fake, the same tests then run against real Stripe test mode, and Claude repairs the fake from the real responses. Stripe-specific code lives in one provider module so the service can be swapped later.

## Requirements
- Python 3.10+, dependencies only: `stripe`, `anthropic`, `pytest`.
- Follow DESIGN.md's layout, interfaces, file formats, and script behaviors exactly. If something in the design is impossible or contradictory, pick the simplest option that keeps the demo foolproof and note it in README.md.
- Foolproof is the top priority: every step that depends on Claude or the network must have the fallback described in the design, exit 0 when the demo can continue, and print exactly what happened. No tracebacks for expected failures; no interactive prompts in demo steps.
- Only Stripe test keys. Refuse any key containing `_live_`. Never print keys or send them (or `client_secret` values) to Claude.
- Do not hard-code guesses about Stripe's error codes into tests. Contract tests compare against fixtures; the real run is the source of truth. Hand-written app tests assert only certain behavior (listed in DESIGN.md 7.3).
- Use `CLAUDE_MODEL` from the environment, defaulting to a current Sonnet model id.

## Order of work
1. `toolkit/`, `providers/stripe_provider.py`, `providers/_template_provider.py`, `app.py`.
2. `conftest.py`, `test_contract.py`, `test_payments.py`, `pytest.ini`.
3. The hand-written backup fake (`backup/stripe/fake_stripe.py`), then generate backup fixtures and sample recordings by running every scenario through it (keep the generator as a small helper, e.g. `tools/build_backup.py`).
4. Scripts: `preflight.py`, `record.py`, `generate_fixtures.py`, `generate_fake.py`, `inject_flaw.py`, `repair_fake.py`, `reset.py`, `save_backup.py`.
5. `README.md`, `DEMO.md` (presenter script with talking points, timings, recovery table), `requirements.txt`, `.env.example`, `.gitignore`.

## Verify before you finish
Run every check in DESIGN.md section 14 that doesn't need real keys, including the repair flow with Claude mocked and a synthetic `real_run.json`, and the RealStripe conversion/error mapping on locally constructed stripe objects. Fix anything that fails. Finish with a short summary of what you verified and anything that still needs a real-key rehearsal.
