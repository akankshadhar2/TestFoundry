"""Template for adding another service. Copy to providers/<name>_provider.py and fill in every TODO.

Then: replace app.py and test_payments.py (or add test_<name>.py with a module-level provider
skip), set DEMO_PROVIDER=<name> in .env, hand-write backup/<name>/fake_<name>.py and run
`python tools/build_backup.py` to generate the backup fixtures and sample recordings from it.
No changes are needed in toolkit/, conftest.py, test_contract.py or any script.

Scenarios call gateway methods by keyword. Declare your gateway's methods in GATEWAY_METHODS so
generate_fake.py / repair_fake.py check the AI fake's signatures against them.
"""

import os

from toolkit.gateway import GatewayError  # noqa: F401  (your real gateway raises this)

NAME = "TODO"                 # e.g. "twilio"; files live under generated/<NAME>/, backup/<NAME>/
DISPLAY_NAME = "TODO"         # e.g. "Twilio"
FAKE_CLASS = "TODO"           # e.g. "FakeTwilio"
ENV_KEY = "TODO_TEST_KEY"     # environment variable holding a TEST credential

# Each scenario: {"name", "description", "steps": [{"call": <gateway method>, "args": {...}}]}
# Use "$stepN.id" to refer to the resource produced by step N.
SCENARIOS = [
    # TODO
]
RECORD_SCENARIOS = []         # TODO: 2-3 scenario names that step 0 records against the real service

INTERFACE_DOC = """TODO: describe each gateway method, its exact signature, what it returns, and the
error contract (GatewayError(http_status, body) where body is the service's exact JSON error)."""

PROMPT_NOTES = """TODO: general domain knowledge for Claude (test tokens, units, lifecycle, ID formats).
Never include the error codes you expect: the real run decides those."""

# Gateway methods the fake must implement: name -> (exact parameter names incl. self, required defaults)
GATEWAY_METHODS = {
    # TODO e.g. "send_message": (["self", "to", "body"], {}),
}

# Optional extra requirements given to Claude when it writes the fake (state machine, ID formats, ...)
FAKE_REQUIREMENTS = [
    # TODO
]

COMPLEXITY_NOTE = "TODO: one talking point about why this resource is hard to fake by hand."


def summarize(kind, http_status, body) -> dict:
    """Reduce one step to the fields that are compared. TODO."""
    raise NotImplementedError


def resource_id(kind, body):
    """The id that "$stepN.id" resolves to (also for error steps if the service returns one). TODO."""
    raise NotImplementedError


def transport_problem(record):
    """Message if a real-run record shows auth/network/server trouble (401/403, >=500, ...), else None. TODO."""
    raise NotImplementedError


def key_problem():
    """Message if the key is missing, a LIVE/production key (refuse!), or malformed; else None. TODO."""
    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        return f"{ENV_KEY} is not set"
    if "_live_" in key:
        return f"{ENV_KEY} is a LIVE key. Refusing."
    raise NotImplementedError("TODO: check the test-key format")


def make_real_gateway():
    """Return an object implementing the gateway interface against the real service. TODO."""
    problem = key_problem()
    if problem:
        raise RuntimeError(problem)
    raise NotImplementedError


def check_real_access():
    """Cheap authenticated call for preflight; raise RuntimeError with a readable message. TODO."""
    raise NotImplementedError


# The plantable flaw: a realistic mistake appended to the generated fake (see DESIGN.md section 9).
FLAW_DESCRIPTION = "TODO: one sentence the presenter says aloud."
FLAW_CODE = "# TODO: code that wraps the fake class generically\n"


def apply_flaw(fixtures_dir):
    """Save <fixture>.preflaw, then rewrite that fixture to expect the flawed behavior. TODO."""
    raise NotImplementedError


def undo_flaw(fixtures_dir):
    """Restore the .preflaw fixture; return True if restored. TODO."""
    raise NotImplementedError
