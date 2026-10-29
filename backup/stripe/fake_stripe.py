"""Hand-written reference fake of the Stripe PaymentIntent/Refund API (the demo's backup).

In-memory, deterministic, stdlib-only. Models the real state machine: PaymentIntents confirmed
with test card tokens, the Charge each confirmation creates, and refunds against that charge's
remaining refundable amount. Bodies carry all the common fields real Stripe returns (nulls
included) so tests and the app see realistic shapes.

Where the exact real error code was uncertain when this was written, it is a best guess; the
real run (pytest --backend=real) is the source of truth and repair_fake.py corrects it.
"""

import copy
import hashlib

from toolkit.gateway import GatewayError

_TEST_CARDS = {
    "pm_card_visa": {"brand": "visa", "last4": "4242", "funding": "credit", "outcome": None},
    "pm_card_visa_debit": {"brand": "visa", "last4": "5556", "funding": "debit", "outcome": None},
    "pm_card_mastercard": {"brand": "mastercard", "last4": "4444", "funding": "credit", "outcome": None},
    "pm_card_amex": {"brand": "amex", "last4": "8431", "funding": "credit", "outcome": None},
    "pm_card_chargeDeclined": {"brand": "visa", "last4": "0002", "funding": "credit",
                               "outcome": "generic_decline"},
    "pm_card_chargeDeclinedInsufficientFunds": {"brand": "visa", "last4": "9995", "funding": "credit",
                                                "outcome": "insufficient_funds"},
}

_DECLINE_MESSAGES = {
    "generic_decline": "Your card was declined.",
    "insufficient_funds": "Your card has insufficient funds.",
}

_ZERO_DECIMAL = {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf",
                 "ugx", "vnd", "vuv", "xaf", "xof", "xpf"}

# Minimum charge amounts in the smallest currency unit.
_MINIMUMS = {"usd": 50, "eur": 50, "gbp": 30, "cad": 50, "aud": 50, "chf": 50, "nzd": 50,
             "sgd": 50, "hkd": 400, "dkk": 250, "nok": 300, "sek": 300, "mxn": 1000,
             "brl": 50, "inr": 50, "pln": 200, "czk": 1500, "jpy": 50, "krw": 500}

_SYMBOLS = {"usd": "$", "cad": "$", "aud": "$", "nzd": "$", "sgd": "$", "hkd": "$", "mxn": "$",
            "eur": "€", "gbp": "£", "jpy": "¥", "inr": "₹", "brl": "R$", "krw": "₩"}

_REQUEST_LOG = "https://dashboard.stripe.com/test/logs/req_{}"


def _format_amount(amount, currency):
    symbol = _SYMBOLS.get(currency, "")
    if currency in _ZERO_DECIMAL:
        text = f"{amount:,}"
    else:
        text = f"{amount / 100:,.2f}"
    return f"{symbol}{text}" if symbol else f"{text} {currency}"


class FakeStripe:
    """In-memory stand-in for the Stripe gateway interface."""

    def __init__(self):
        self._seq = 0
        self._clock = 1_718_000_000
        self._payment_intents = {}
        self._charges = {}
        self._refunds = {}

    # ---- helpers --------------------------------------------------------------

    def _id(self, prefix, length=24):
        self._seq += 1
        digest = hashlib.sha256(f"{prefix}:{self._seq}".encode()).hexdigest()
        alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        n = int(digest, 16)
        chars = []
        while len(chars) < length:
            n, r = divmod(n, len(alphabet))
            chars.append(alphabet[r])
        return f"{prefix}_3Fk" + "".join(chars)[: length - 3]

    def _now(self):
        self._clock += 1
        return self._clock

    def _error(self, status, err_type, message, code=None, param=None, **extra):
        body = {"error": {"code": code, "doc_url": f"https://stripe.com/docs/error-codes/{code.replace('_', '-')}"
                          if code else None, "message": message, "param": param,
                          "request_log_url": _REQUEST_LOG.format(self._id("req", 14)[4:]),
                          "type": err_type}}
        body["error"].update(extra)
        body["error"] = {k: v for k, v in body["error"].items() if v is not None or k in ("code", "param")}
        raise GatewayError(status, body)

    def _missing(self, kind, value, param):
        self._error(404, "invalid_request_error", f"No such {kind}: '{value}'",
                    code="resource_missing", param=param)

    def _payment_method(self, token):
        card = _TEST_CARDS[token]
        return {
            "id": self._id("pm"),
            "object": "payment_method",
            "allow_redisplay": "unspecified",
            "billing_details": {"address": {"city": None, "country": None, "line1": None, "line2": None,
                                            "postal_code": None, "state": None},
                                "email": None, "name": None, "phone": None, "tax_id": None},
            "card": {
                "brand": card["brand"],
                "checks": {"address_line1_check": None, "address_postal_code_check": None,
                           "cvc_check": None},
                "country": "US",
                "display_brand": card["brand"],
                "exp_month": 12,
                "exp_year": 2034,
                "fingerprint": hashlib.sha256(token.encode()).hexdigest()[:16],
                "funding": card["funding"],
                "generated_from": None,
                "last4": card["last4"],
                "networks": {"available": [card["brand"]], "preferred": None},
                "regulated_status": "unregulated",
                "three_d_secure_usage": {"supported": True},
                "wallet": None,
            },
            "created": self._now(),
            "customer": None,
            "livemode": False,
            "metadata": {},
            "type": "card",
        }

    def _new_payment_intent(self, amount, currency, pm_id):
        pi_id = self._id("pi")
        return {
            "id": pi_id,
            "object": "payment_intent",
            "amount": amount,
            "amount_capturable": 0,
            "amount_details": {"tip": {}},
            "amount_received": 0,
            "application": None,
            "application_fee_amount": None,
            "automatic_payment_methods": None,
            "canceled_at": None,
            "cancellation_reason": None,
            "capture_method": "automatic_async",
            "client_secret": f"{pi_id}_secret_{self._id('cs', 25)[3:]}",
            "confirmation_method": "automatic",
            "created": self._now(),
            "currency": currency,
            "customer": None,
            "description": None,
            "last_payment_error": None,
            "latest_charge": None,
            "livemode": False,
            "metadata": {},
            "next_action": None,
            "on_behalf_of": None,
            "payment_method": pm_id,
            "payment_method_configuration_details": None,
            "payment_method_options": {"card": {"installments": None, "mandate_options": None,
                                                "network": None, "request_three_d_secure": "automatic"}},
            "payment_method_types": ["card"],
            "processing": None,
            "receipt_email": None,
            "review": None,
            "setup_future_usage": None,
            "shipping": None,
            "source": None,
            "statement_descriptor": None,
            "statement_descriptor_suffix": None,
            "status": "requires_payment_method",
            "transfer_data": None,
            "transfer_group": None,
        }

    # ---- gateway interface ----------------------------------------------------

    def create_payment(self, amount: int, currency: str, payment_method: str) -> dict:
        if not isinstance(amount, int) or isinstance(amount, bool):
            self._error(400, "invalid_request_error", "Invalid integer: amount",
                        code="parameter_invalid_integer", param="amount")
        currency = str(currency or "").lower()
        if currency not in _MINIMUMS and currency not in _ZERO_DECIMAL:
            self._error(400, "invalid_request_error", f"Invalid currency: {currency}.",
                        code="parameter_invalid_string", param="currency")
        minimum = _MINIMUMS.get(currency, 1)
        if amount < minimum:
            self._error(400, "invalid_request_error",
                        f"Amount must be at least {_format_amount(minimum, currency)} {currency}",
                        code="amount_too_small", param="amount")
        if payment_method not in _TEST_CARDS:
            self._missing("PaymentMethod", payment_method, "payment_method")

        pm = self._payment_method(payment_method)
        pi = self._new_payment_intent(amount, currency, pm["id"])
        outcome = _TEST_CARDS[payment_method]["outcome"]
        charge_id = self._id("ch")
        succeeded = outcome is None
        self._charges[charge_id] = {"id": charge_id, "amount": amount if succeeded else 0,
                                    "amount_refunded": 0, "currency": currency,
                                    "payment_intent": pi["id"], "succeeded": succeeded}
        pi["latest_charge"] = charge_id
        self._payment_intents[pi["id"]] = pi

        if succeeded:
            pi["status"] = "succeeded"
            pi["amount_received"] = amount
            return copy.deepcopy(pi)

        message = _DECLINE_MESSAGES.get(outcome, "Your card was declined.")
        pi["payment_method"] = None
        pi["last_payment_error"] = {
            "advice_code": "try_again_later" if outcome == "insufficient_funds" else None,
            "charge": charge_id,
            "code": "card_declined",
            "decline_code": outcome,
            "doc_url": "https://stripe.com/docs/error-codes/card-declined",
            "message": message,
            "payment_method": pm,
            "type": "card_error",
        }
        if pi["last_payment_error"]["advice_code"] is None:
            del pi["last_payment_error"]["advice_code"]
        self._error(402, "card_error", message, code="card_declined", param=None,
                    decline_code=outcome, charge=charge_id,
                    payment_intent=copy.deepcopy(pi), payment_method=copy.deepcopy(pm))

    def refund_payment(self, payment_id: str, amount: int | None = None) -> dict:
        pi = self._payment_intents.get(payment_id)
        if pi is None:
            self._missing("payment_intent", payment_id, "payment_intent")
        if amount is not None and (not isinstance(amount, int) or isinstance(amount, bool) or amount < 1):
            self._error(400, "invalid_request_error", "Invalid positive integer",
                        code="parameter_invalid_integer", param="amount")
        charge = self._charges.get(pi["latest_charge"])
        if pi["status"] != "succeeded" or charge is None or not charge["succeeded"]:
            self._error(400, "invalid_request_error",
                        f"This PaymentIntent ({payment_id}) does not have a successful charge to refund.",
                        code="charge_not_refundable", param=None)
        remaining = charge["amount"] - charge["amount_refunded"]
        if remaining <= 0:
            self._error(400, "invalid_request_error", f"Charge {charge['id']} has already been refunded.",
                        code="charge_already_refunded", param=None, charge=charge["id"])
        requested = remaining if amount is None else amount
        if requested > remaining:
            self._error(400, "invalid_request_error",
                        f"Refund amount ({_format_amount(requested, charge['currency'])}) is greater than "
                        f"unrefunded amount on charge ({_format_amount(remaining, charge['currency'])})",
                        code="amount_too_large", param="amount")

        charge["amount_refunded"] += requested
        refund = {
            "id": self._id("re"),
            "object": "refund",
            "amount": requested,
            "balance_transaction": self._id("txn"),
            "charge": charge["id"],
            "created": self._now(),
            "currency": charge["currency"],
            "destination_details": {"card": {"reference_status": "pending",
                                             "reference_type": "acquirer_reference_number",
                                             "type": "refund"},
                                    "type": "card"},
            "metadata": {},
            "payment_intent": payment_id,
            "reason": None,
            "receipt_number": None,
            "source_transfer_reversal": None,
            "status": "succeeded",
            "transfer_reversal": None,
        }
        self._refunds[refund["id"]] = refund
        return copy.deepcopy(refund)
