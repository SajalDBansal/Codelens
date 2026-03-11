"""Price calculations used by checkout."""


def subtotal(prices: list[float]) -> float:
    """Add the prices of all items in the order."""
    return sum(prices)


def apply_discount(amount: float, percent: float) -> float:
    """Reduce the subtotal by a percentage between zero and one hundred."""
    if not 0 <= percent <= 100:
        raise ValueError("Discount must be between 0 and 100 percent")
    return amount * (1 - percent / 100)


def add_tax(amount: float, percent: float) -> float:
    """Apply percentage tax to an already discounted amount."""
    return amount * (1 + percent / 100)
