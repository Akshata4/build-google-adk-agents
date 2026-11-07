"""Discounts and tax."""

from decimal import ROUND_HALF_UP, Decimal

# code -> (kind, value, minimum subtotal)
DISCOUNT_CODES = {
    "SAVE10": ("percent", Decimal(10), Decimal(0)),
    "FIVEOFF": ("fixed", Decimal(5), Decimal(20)),
    "HALF": ("percent", Decimal(50), Decimal(100)),
}

CENT = Decimal("0.01")


def apply_discount(subtotal: Decimal, code: str | None) -> Decimal:
    """Applies a discount code. Codes are case-insensitive.

    Unknown codes raise ValueError. A code whose minimum subtotal isn't met is
    ignored. The result never goes below zero.
    """
    if not code:
        return subtotal
    try:
        kind, value, minimum = DISCOUNT_CODES[code.upper()]
    except KeyError:
        raise ValueError(f"unknown discount code: {code}") from None
    if subtotal < minimum:
        return subtotal
    if kind == "percent":
        discounted = subtotal * (100 - value) / 100
    else:
        discounted = subtotal - value
    return max(discounted, Decimal(0))


def add_tax(amount: Decimal, rate: Decimal) -> Decimal:
    """Adds tax at rate (e.g. Decimal("0.08") for 8%), rounded half up to cents."""
    if not Decimal(0) <= rate <= Decimal(1):
        raise ValueError("rate must be between 0 and 1")
    return (amount * (1 + rate)).quantize(CENT, rounding=ROUND_HALF_UP)
