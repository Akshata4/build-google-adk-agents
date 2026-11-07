"""A shopping cart that tracks items and quantities."""

from dataclasses import dataclass
from decimal import Decimal

from .pricing import add_tax, apply_discount


@dataclass(frozen=True)
class Item:
    sku: str
    name: str
    unit_price: Decimal

    def __post_init__(self):
        if self.unit_price < 0:
            raise ValueError("unit_price can't be negative")


class Cart:
    MAX_QUANTITY = 99

    def __init__(self):
        self._lines: dict[str, tuple[Item, int]] = {}
        self.discount_code: str | None = None

    def add(self, item: Item, quantity: int = 1) -> None:
        """Adds quantity of item; repeated adds of the same SKU accumulate."""
        if quantity < 1:
            raise ValueError("quantity must be at least 1")
        current = self._lines.get(item.sku, (item, 0))[1]
        if current + quantity > self.MAX_QUANTITY:
            raise ValueError(f"at most {self.MAX_QUANTITY} of one item")
        self._lines[item.sku] = (item, current + quantity)

    def remove(self, sku: str, quantity: int | None = None) -> None:
        """Removes quantity of a SKU, or the whole line when quantity is None."""
        if sku not in self._lines:
            raise KeyError(sku)
        item, current = self._lines[sku]
        if quantity is None or quantity >= current:
            del self._lines[sku]
        else:
            self._lines[sku] = (item, current - quantity)

    def quantity(self, sku: str) -> int:
        return self._lines.get(sku, (None, 0))[1]

    @property
    def item_count(self) -> int:
        return sum(q for _, q in self._lines.values())

    def subtotal(self) -> Decimal:
        return sum(
            (item.unit_price * q for item, q in self._lines.values()), Decimal(0)
        )

    def total(self, tax_rate: Decimal = Decimal(0)) -> Decimal:
        """Subtotal, minus the discount, plus tax, rounded to cents."""
        discounted = apply_discount(self.subtotal(), self.discount_code)
        return add_tax(discounted, tax_rate)
