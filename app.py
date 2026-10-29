"""The code under test: a tiny payments module that talks to a gateway (real Stripe or a fake)."""

from dataclasses import dataclass

from toolkit.gateway import GatewayError

FRIENDLY_DECLINES = {
    "insufficient_funds": "Your card has insufficient funds.",
}
DEFAULT_DECLINE = "Your card was declined."


@dataclass
class ChargeResult:
    ok: bool
    payment_id: str | None
    status: str | None
    decline_code: str | None = None
    message: str = ""


@dataclass
class RefundResult:
    ok: bool
    refund_id: str | None
    status: str | None
    amount: int | None
    message: str = ""


class PaymentError(Exception):
    """A non-card failure (bad request, auth, network) the caller can't fix by changing cards."""


def charge(gateway, amount_cents: int, currency: str, payment_method: str) -> ChargeResult:
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        raise ValueError(f"amount_cents must be a positive integer, got {amount_cents!r}")
    try:
        pi = gateway.create_payment(amount=amount_cents, currency=currency, payment_method=payment_method)
    except GatewayError as e:
        err = e.error
        if err.get("type") == "card_error":
            decline = err.get("decline_code") or err.get("code")
            pi = err.get("payment_intent") if isinstance(err.get("payment_intent"), dict) else {}
            return ChargeResult(ok=False, payment_id=pi.get("id"), status=pi.get("status"),
                                decline_code=decline,
                                message=FRIENDLY_DECLINES.get(decline, DEFAULT_DECLINE))
        raise PaymentError(e.message) from e
    status = pi.get("status")
    return ChargeResult(ok=status == "succeeded", payment_id=pi.get("id"), status=status,
                        message="" if status == "succeeded" else f"Payment is {status}.")


def refund(gateway, payment_id: str, amount_cents: int | None = None) -> RefundResult:
    try:
        r = gateway.refund_payment(payment_id=payment_id, amount=amount_cents)
    except GatewayError as e:
        return RefundResult(ok=False, refund_id=None, status=None, amount=None, message=e.message)
    status = r.get("status")
    return RefundResult(ok=status in ("succeeded", "pending"), refund_id=r.get("id"), status=status,
                        amount=r.get("amount"))
