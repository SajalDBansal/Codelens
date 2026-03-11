"""Checkout combines the pricing helpers in a fixed order."""
from pricing import subtotal, apply_discount, add_tax


def checkout(prices: list[float], discount: float = 0, tax: float = 0) -> float:
    """Return the final order total, rounded to two decimal places."""
    amount = subtotal(prices)
    discounted = apply_discount(amount, discount)
    total = add_tax(discounted, tax)
    return round(total, 2)
