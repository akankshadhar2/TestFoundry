"""Hand-written tests of app.py. They assert only behavior that is certain for Stripe test mode."""

import pytest

from toolkit import config

if config.load_provider().NAME != "stripe":
    pytest.skip("Stripe app tests (DEMO_PROVIDER is not stripe)", allow_module_level=True)

from app import charge, refund  # noqa: E402


def test_visa_charge_succeeds(gateway):
    result = charge(gateway, 2000, "usd", "pm_card_visa")
    assert result.ok
    assert result.status == "succeeded"
    assert result.payment_id.startswith("pi_")


def test_declined_card(gateway):
    result = charge(gateway, 2000, "usd", "pm_card_chargeDeclined")
    assert not result.ok
    assert result.decline_code == "generic_decline"
    assert result.message == "Your card was declined."


def test_insufficient_funds(gateway):
    result = charge(gateway, 2000, "usd", "pm_card_chargeDeclinedInsufficientFunds")
    assert not result.ok
    assert result.decline_code == "insufficient_funds"
    assert "insufficient funds" in result.message


def test_charge_then_full_refund(gateway):
    paid = charge(gateway, 2000, "usd", "pm_card_visa")
    assert paid.ok
    assert refund(gateway, paid.payment_id).ok


def test_refund_twice_second_fails(gateway):
    paid = charge(gateway, 2000, "usd", "pm_card_visa")
    assert refund(gateway, paid.payment_id).ok
    assert not refund(gateway, paid.payment_id).ok


def test_zero_amount_rejected_before_calling_gateway(gateway):
    with pytest.raises(ValueError):
        charge(gateway, 0, "usd", "pm_card_visa")
