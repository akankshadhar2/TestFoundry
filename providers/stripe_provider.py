"""Stripe provider: ALL Stripe-specific knowledge lives here.

The pipeline (toolkit/, conftest.py, test_contract.py and every script) only uses the names
listed in DESIGN.md section 5, so another service can be demoed by writing another provider.
"""

import json
import os
from pathlib import Path

from toolkit.gateway import GatewayError
from toolkit.scenarios import load_json, save_json

NAME = "stripe"
DISPLAY_NAME = "Stripe"
FAKE_CLASS = "FakeStripe"
ENV_KEY = "STRIPE_TEST_KEY"

# ---- scenarios ------------------------------------------------------------------

def _create(pm, amount=2000, currency="usd"):
    return {"call": "create_payment", "args": {"amount": amount, "currency": currency, "payment_method": pm}}


def _refund(amount=None, ref="$step0.id"):
    args = {"payment_id": ref}
    if amount is not None:
        args["amount"] = amount
    return {"call": "refund_payment", "args": args}


SCENARIOS = [
    {"name": "successful_charge", "description": "Charge $20.00 to a Visa test card.",
     "steps": [_create("pm_card_visa")]},
    {"name": "declined_card", "description": "Charge a card that is always declined.",
     "steps": [_create("pm_card_chargeDeclined")]},
    {"name": "full_refund", "description": "Charge a Visa card, then refund it in full.",
     "steps": [_create("pm_card_visa"), _refund()]},
    {"name": "insufficient_funds", "description": "Charge a card that is declined for insufficient funds.",
     "steps": [_create("pm_card_chargeDeclinedInsufficientFunds")]},
    {"name": "partial_refund", "description": "Charge $20.00, then refund $5.00 of it.",
     "steps": [_create("pm_card_visa"), _refund(500)]},
    {"name": "refund_declined_payment", "description": "Try to refund a payment whose card was declined.",
     "steps": [_create("pm_card_chargeDeclined"), _refund()]},
    {"name": "double_refund", "description": "Refund a payment in full, then try to refund it again.",
     "steps": [_create("pm_card_visa"), _refund(), _refund()]},
    {"name": "over_refund", "description": "Charge $20.00, then try to refund $50.00.",
     "steps": [_create("pm_card_visa"), _refund(5000)]},
    {"name": "below_minimum_amount", "description": "Try to charge 10 cents (below the minimum charge).",
     "steps": [_create("pm_card_visa", amount=10)]},
]
RECORD_SCENARIOS = ["successful_charge", "declined_card", "full_refund"]

# ---- text for Claude ------------------------------------------------------------

INTERFACE_DOC = """\
Gateway interface (Python). Both the real Stripe client and the fake implement exactly:

    create_payment(self, amount: int, currency: str, payment_method: str) -> dict
        Creates AND confirms a Stripe PaymentIntent (card payment methods only).
        Equivalent real call: stripe.PaymentIntent.create(amount=..., currency=...,
            payment_method=..., confirm=True,
            automatic_payment_methods={"enabled": True, "allow_redirects": "never"})
        Returns the PaymentIntent JSON as a plain dict.

    refund_payment(self, payment_id: str, amount: int | None = None) -> dict
        Refunds a PaymentIntent. amount=None means a full refund of what remains.
        Equivalent real call: stripe.Refund.create(payment_intent=payment_id[, amount=amount])
        Returns the Refund JSON as a plain dict.

Parameter names are part of the contract: callers always pass arguments by keyword.

Errors: every failure raises toolkit.gateway.GatewayError(http_status: int, body: dict), where
body is the exact JSON error body Stripe sends: {"error": {"type": ..., "code": ...,
"decline_code": ..., "message": ..., "param": ..., ...}}. When a card is declined while
confirming, Stripe's error body includes the full PaymentIntent object under
error.payment_intent (tests read error.payment_intent.id to refer to it later).
"""

PROMPT_NOTES = """\
Stripe domain notes (general knowledge):
- Amounts are integers in the currency's smallest unit (cents for usd: 2000 = $20.00).
  Zero-decimal currencies such as jpy have no minor unit.
- Test payment method tokens: pm_card_visa and pm_card_mastercard always succeed;
  pm_card_chargeDeclined is always declined (a generic decline);
  pm_card_chargeDeclinedInsufficientFunds is declined because of insufficient funds.
  Confirming with a token creates a new PaymentMethod object (pm_...) attached to the PaymentIntent.
- Stripe enforces a minimum charge amount per currency (for usd it is $0.50).
- PaymentIntent lifecycle: requires_payment_method -> requires_confirmation -> (requires_action) ->
  processing -> succeeded, or canceled. A failed confirmation returns the PaymentIntent to
  requires_payment_method with last_payment_error set. Each confirmation attempt creates a
  Charge (ch_...); latest_charge points at it.
- Refunds (re_...) apply to the PaymentIntent's successful charge. Several partial refunds are
  allowed until the charged amount is used up; Stripe tracks how much has been refunded.
- ID prefixes: pi_ PaymentIntent, ch_ Charge, re_ Refund, pm_ PaymentMethod, txn_ balance transaction.
  PaymentIntents have a client_secret of the form <pi id>_secret_<random>.
- Test-mode objects have livemode=false. Timestamps are Unix seconds.
- Successful API calls return HTTP 200. Use your knowledge of the real Stripe API for exact
  error types, codes, decline codes, messages and HTTP statuses.
"""

# Gateway methods the fake must implement: name -> (exact parameter names, required defaults).
GATEWAY_METHODS = {
    "create_payment": (["self", "amount", "currency", "payment_method"], {}),
    "refund_payment": (["self", "payment_id", "amount"], {"amount": None}),
}

# Extra, service-specific requirements generate_fake.py gives Claude for the fake.
FAKE_REQUIREMENTS = [
    "Model the real state machine: PaymentIntents, the Charge each confirmation creates, refunds, "
    "and the remaining refundable amount on the charge.",
    "Card-decline error bodies include the full PaymentIntent under error.payment_intent.",
    "Deterministic IDs with the real prefixes (pi_, ch_, re_, pm_).",
]

COMPLEXITY_NOTE = """\
A Stripe PaymentIntent is a state machine, not a simple record:
  requires_payment_method -> requires_confirmation -> requires_action -> processing -> succeeded
                         \\-> canceled        (a declined card sends it back to requires_payment_method)
Each confirmation creates a Charge; refunds hang off that Charge and track a refundable balance.
Hand-writing a faithful fake of all this is slow and error-prone. Let's have Claude do it."""

# ---- summaries ------------------------------------------------------------------

def summarize(kind, http_status, body) -> dict:
    body = body if isinstance(body, dict) else {}
    if kind == "success":
        return {"ok": True, "object": body.get("object"), "status": body.get("status"),
                "amount": body.get("amount"), "currency": body.get("currency")}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    return {"ok": False, "http_status": http_status, "error_type": err.get("type"),
            "error_code": err.get("code"), "decline_code": err.get("decline_code")}


def resource_id(kind, body):
    if not isinstance(body, dict):
        return None
    if kind == "success":
        return body.get("id")
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    pi = err.get("payment_intent")
    if isinstance(pi, dict):
        return pi.get("id")
    return pi if isinstance(pi, str) else None


def transport_problem(record):
    """A message if a real run hit network/auth trouble (so the result says nothing about Stripe)."""
    for step in record.get("steps", []):
        if step.get("kind") != "error":
            continue
        status = step.get("http_status") or 0
        err = (step.get("body") or {}).get("error") or {}
        etype = err.get("type") if isinstance(err, dict) else None
        if status in (401, 403):
            return (f"Stripe rejected the API key (HTTP {status}). Check {ENV_KEY} in .env "
                    "(Dashboard -> Developers -> API keys, test mode).")
        if status == 429:
            return "Stripe is rate-limiting requests (HTTP 429). Wait a moment and retry."
        if etype == "api_connection_error":
            return "Could not reach Stripe (network problem)."
        if status >= 500:
            return f"Stripe returned a server error (HTTP {status})."
    return None

# ---- keys and the real gateway --------------------------------------------------

def key_problem():
    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        return f"{ENV_KEY} is not set (copy .env.example to .env and add your Stripe TEST secret key)"
    if "_live_" in key:
        return f"{ENV_KEY} is a LIVE key. Refusing: this demo only ever uses Stripe test keys (sk_test_...)."
    if not (key.startswith("sk_test_") or key.startswith("rk_test_")):
        return f"{ENV_KEY} does not look like a Stripe test key (expected sk_test_... or rk_test_...)"
    return None


def _to_plain(obj):
    """Stripe object -> plain dict, robust across stripe-python versions."""
    if isinstance(obj, dict) and type(obj) is dict:
        return obj
    try:
        return json.loads(str(obj))
    except (TypeError, ValueError):
        pass
    for attr in ("to_dict_recursive", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return json.loads(json.dumps(fn(), default=str))
            except (TypeError, ValueError):
                continue
    return json.loads(json.dumps(dict(obj), default=str))


class RealStripe:
    """Talks to real Stripe (test mode) through the same interface as the fake."""

    def __init__(self, key: str):
        import stripe
        self._stripe = stripe
        stripe.api_key = key
        self._error_cls = getattr(stripe, "StripeError", None) or stripe.error.StripeError

    def _call(self, fn, **params):
        try:
            return _to_plain(fn(**params))
        except self._error_cls as e:
            raise GatewayError(
                getattr(e, "http_status", None) or 503,
                getattr(e, "json_body", None) or {"error": {"type": "api_connection_error", "message": str(e)}},
            ) from None

    def create_payment(self, amount: int, currency: str, payment_method: str) -> dict:
        return self._call(self._stripe.PaymentIntent.create, amount=amount, currency=currency,
                          payment_method=payment_method, confirm=True,
                          # Newer Stripe accounts reject payment_method_types; dynamic payment methods
                          # with redirects off lets a card confirm without a return_url.
                          automatic_payment_methods={"enabled": True, "allow_redirects": "never"})

    def refund_payment(self, payment_id: str, amount: int | None = None) -> dict:
        params = {"payment_intent": payment_id}
        if amount is not None:
            params["amount"] = amount
        return self._call(self._stripe.Refund.create, **params)


def make_real_gateway():
    problem = key_problem()
    if problem:
        raise RuntimeError(problem)
    return RealStripe(os.environ[ENV_KEY].strip())


def check_real_access():
    """Cheap authenticated call. Raises RuntimeError with a readable message on failure."""
    problem = key_problem()
    if problem:
        raise RuntimeError(problem)
    import stripe
    stripe.api_key = os.environ[ENV_KEY].strip()
    err_cls = getattr(stripe, "StripeError", None) or stripe.error.StripeError
    try:
        balance = stripe.Balance.retrieve()
    except err_cls as e:
        status = getattr(e, "http_status", None)
        kind = type(e).__name__
        raise RuntimeError(f"{kind}{f' (HTTP {status})' if status else ''}: "
                           f"{getattr(e, 'user_message', None) or str(e).splitlines()[0][:160]}") from None
    if getattr(balance, "livemode", False):
        raise RuntimeError("Stripe reports livemode=true for this key. Refusing.")
    return "test-mode account reachable"

# ---- the plantable flaw (DESIGN.md section 9) ------------------------------------

FLAW_SCENARIO = "refund_declined_payment"
FLAW_DESCRIPTION = ("I've planted a realistic mistake: the fake now believes a declined payment "
                    "can be refunded.")

FLAW_CODE = '''
# Planted for the demo: this fake believes a declined payment can be refunded.
from toolkit.gateway import GatewayError as _DemoFlawGatewayError

_demo_flaw_declined = {}  # id(fake instance) -> {payment_intent id: (amount, currency)}


def _demo_flaw_install(cls):
    original_create, original_refund = cls.create_payment, cls.refund_payment

    def create_payment(self, amount, currency, payment_method):
        known = _demo_flaw_declined.setdefault(id(self), {})
        try:
            result = original_create(self, amount=amount, currency=currency, payment_method=payment_method)
        except _DemoFlawGatewayError as e:
            err = (e.body or {}).get("error") or {}
            pi = err.get("payment_intent") or {}
            if err.get("type") == "card_error" and isinstance(pi, dict) and pi.get("id"):
                known[pi["id"]] = (amount, currency)
            raise
        if isinstance(result, dict):
            known.pop(result.get("id"), None)
        return result

    def refund_payment(self, payment_id, amount=None):
        known = _demo_flaw_declined.get(id(self), {})
        if payment_id in known:
            paid, currency = known[payment_id]
            return {
                "id": "re_demoflaw" + payment_id[-14:], "object": "refund",
                "amount": paid if amount is None else amount, "balance_transaction": None,
                "charge": None, "created": 1700000000, "currency": currency,
                "destination_details": None, "metadata": {}, "payment_intent": payment_id,
                "reason": None, "receipt_number": None, "source_transfer_reversal": None,
                "status": "succeeded", "transfer_reversal": None,
            }
        return original_refund(self, payment_id=payment_id, amount=amount)

    cls.create_payment, cls.refund_payment = create_payment, refund_payment


_demo_flaw_install(FakeStripe)
'''


def apply_flaw(fixtures_dir):
    """Make the refund_declined_payment fixture expect the flawed success (so step 3 stays green).

    Saves the original as <fixture>.preflaw (once). Returns a short description of the change.
    """
    path = Path(fixtures_dir) / f"{FLAW_SCENARIO}.json"
    if not path.is_file():
        raise FileNotFoundError(f"fixture {path.name} not found")
    preflaw = path.with_name(path.name + ".preflaw")
    if not preflaw.exists():
        preflaw.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    fixture = load_json(preflaw)
    steps = fixture["steps"]
    create = steps[0]
    pi_id = resource_id(create.get("kind"), create.get("body")) or "pi_unknown"
    amount = create["args"].get("amount")
    currency = create["args"].get("currency")
    steps[1] = {
        "call": "refund_payment", "args": steps[1].get("args", {"payment_id": "$step0.id"}),
        "kind": "success", "http_status": 200,
        "body": {"id": "re_demoflaw" + pi_id[-14:], "object": "refund", "amount": amount,
                 "balance_transaction": None, "charge": None, "created": 1700000000,
                 "currency": currency, "destination_details": None, "metadata": {},
                 "payment_intent": pi_id, "reason": None, "receipt_number": None,
                 "source_transfer_reversal": None, "status": "succeeded", "transfer_reversal": None},
    }
    fixture["flaw_planted"] = True
    save_json(path, fixture)
    return f"{path.name}: refund step now expects a successful refund of {amount} {currency}"


def undo_flaw(fixtures_dir):
    """Restore the .preflaw fixture. Returns True if something was restored."""
    path = Path(fixtures_dir) / f"{FLAW_SCENARIO}.json"
    preflaw = path.with_name(path.name + ".preflaw")
    if not preflaw.is_file():
        return False
    preflaw.replace(path)
    return True
