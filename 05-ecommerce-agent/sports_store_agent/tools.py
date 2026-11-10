"""Customer tools: the signed-in customer's profile, cart and orders.

The customer is the ADK session's user_id (`tool_context.user_id`), which this
code passes to the `customer_toolset` SQL tools in MCP Toolbox. None of these
functions has a customer parameter, so the model can't read or change another
customer's data, whatever the conversation says. In production the user_id
comes from the app's login.

Each SQL write is one atomic statement that reports what happened; the
functions here turn that into a clear status for the model, with the
information it needs to recover (e.g. the variants that exist, when a size or
color doesn't).
"""

import functools
import json
import logging

from google.adk.tools import FunctionTool, ToolContext
from toolbox_core import ToolboxClient

from .config import MAX_CART_QUANTITY, TOOLBOX_URL

logger = logging.getLogger(__name__)

NO_ACCOUNT = {
    "status": "error",
    "message": "There is no customer account for this user, so carts and orders "
    "are unavailable. Product search and store information still work.",
}


class StoreError(Exception):
    """The database refused a statement (Toolbox returns this as text)."""


async def _run(tool_name: str, **params) -> list[dict]:
    """Calls a Toolbox tool; returns its rows (Toolbox sends one row as an object)."""
    async with ToolboxClient(TOOLBOX_URL) as client:
        tool = await client.load_tool(tool_name)
        raw = await tool(**params)
    try:
        data = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        raise StoreError(raw) from None
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def _handles_store_errors(func):
    """Returns an error status instead of raising, so the conversation goes on.

    Database and connection details are logged, not shown to the model.
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except StoreError as e:
            logger.warning("%s refused: %s", func.__name__, e)
            return {
                "status": "error",
                "message": "The store system couldn't complete that request.",
            }
        except OSError as e:  # Toolbox not reachable
            logger.error("%s: store system unavailable: %s", func.__name__, e)
            return {
                "status": "error",
                "message": "The store system is unavailable right now; please try "
                "again shortly.",
            }

    return wrapper


async def _customer_rows(
    tool_name: str, tool_context: ToolContext, **params
) -> list[dict]:
    return await _run(tool_name, customer=tool_context.user_id, **params)


async def _has_account(tool_context: ToolContext) -> bool:
    return bool(await _customer_rows("get-customer-profile", tool_context))


@_handles_store_errors
async def get_my_profile(tool_context: ToolContext) -> dict:
    """Returns the customer's name and delivery address."""
    rows = await _customer_rows("get-customer-profile", tool_context)
    return {"status": "ok", "profile": rows[0]} if rows else NO_ACCOUNT


@_handles_store_errors
async def find_nearest_stores(tool_context: ToolContext) -> dict:
    """Returns the 3 stores closest to the customer's address, with distances in metres."""
    rows = await _customer_rows("find-nearest-stores", tool_context)
    return {"status": "ok", "stores": rows} if rows else NO_ACCOUNT


@_handles_store_errors
async def add_to_cart(
    product_name: str, size: str, color: str, quantity: int, tool_context: ToolContext
) -> dict:
    """Adds a product in a given size and color to the customer's cart.

    Args:
        product_name: The exact product name, as returned by the catalog tools.
        size: The size, e.g. "42" or "M".
        color: The color, e.g. "Black/White".
        quantity: Units to add, 1 to 10.
    """
    if not 1 <= quantity <= MAX_CART_QUANTITY:
        return {
            "status": "error",
            "message": f"Quantity must be between 1 and {MAX_CART_QUANTITY}.",
        }
    try:
        (row,) = await _customer_rows(
            "add-to-cart",
            tool_context,
            product_name=product_name,
            size=size,
            color=color,
            quantity=quantity,
        )
    except StoreError as e:
        if "cart_items_quantity_check" in str(e):
            return {
                "status": "error",
                "message": f"A cart line can hold at most {MAX_CART_QUANTITY} units.",
            }
        raise
    if not row["customer_found"]:
        return NO_ACCOUNT
    if not row["variant_found"]:
        details = await _run("get-product-details", product_name=product_name)
        return {
            "status": "error",
            "message": f"{product_name} isn't available in size {size}, color {color}.",
            "available_variants": details[0]["variants"] if details else [],
            "hint": "Ask the customer to choose one of the available variants."
            if details
            else "No product has that name; search the catalog for the exact name.",
        }
    return {
        "status": "ok",
        "added": f"{quantity} x {product_name} (size {size}, {color})",
        "unit_price": row["unit_price"],
        "quantity_in_cart": row["quantity_in_cart"],
    }


@_handles_store_errors
async def view_cart(tool_context: ToolContext) -> dict:
    """Returns the items in the customer's cart and the cart total."""
    rows = await _customer_rows("view-cart", tool_context)
    if not rows:
        if not await _has_account(tool_context):
            return NO_ACCOUNT
        return {"status": "ok", "items": [], "cart_total": 0}
    total = rows[0]["cart_total"]
    items = [{k: v for k, v in r.items() if k != "cart_total"} for r in rows]
    return {"status": "ok", "items": items, "cart_total": total}


@_handles_store_errors
async def remove_from_cart(
    product_name: str, size: str, color: str, tool_context: ToolContext
) -> dict:
    """Removes a product (size and color) from the customer's cart.

    Args:
        product_name: The exact product name.
        size: The size of the cart line to remove.
        color: The color of the cart line to remove.
    """
    (row,) = await _customer_rows(
        "remove-from-cart",
        tool_context,
        product_name=product_name,
        size=size,
        color=color,
    )
    if row["lines_removed"]:
        return {"status": "ok", "removed": f"{product_name} (size {size}, {color})"}
    return {
        "status": "error",
        "message": "That item isn't in the cart; view the cart for the exact lines.",
    }


@_handles_store_errors
async def place_order(
    store_name: str, delivery_method: str, tool_context: ToolContext
) -> dict:
    """Orders everything in the customer's cart from one store.

    Stock is checked at that store; the order is created, stock reduced and the
    cart emptied together, or nothing changes. The customer is asked to confirm
    before this runs.

    Args:
        store_name: The store fulfilling the order (see find_nearest_stores).
        delivery_method: "Standard Home Delivery", "Express Delivery" or "Store Pickup".
    """
    (row,) = await _customer_rows(
        "place-order",
        tool_context,
        store_name=store_name,
        delivery_method=delivery_method,
    )
    if row["order_id"]:
        return {
            "status": "ok",
            "order_id": row["order_id"],
            "items_total": row["items_total"],
            "shipping_cost": row["shipping_cost"],
            "total_amount": row["total_amount"],
            "shipping_address": row["shipping_address"] or f"pickup at {store_name}",
            "lines_ordered": row["lines_ordered"],
        }
    if not row["customer_found"]:
        return NO_ACCOUNT
    if not row["store_found"]:
        return {
            "status": "error",
            "message": f"There's no store called {store_name}.",
            "stores": [s["name"] for s in await _run("list-stores")],
        }
    if not row["delivery_method_found"]:
        methods = await _run("list-delivery-methods", store_name=store_name)
        return {
            "status": "error",
            "message": f"{store_name} doesn't offer {delivery_method}.",
            "delivery_methods": [m["name"] for m in methods],
        }
    if not row["cart_lines"]:
        return {"status": "error", "message": "The cart is empty."}
    return {
        "status": "error",
        "message": f"Not enough stock at {store_name}; nothing was ordered. "
        "Try another store, or change these items.",
        "out_of_stock": row["out_of_stock"],
    }


@_handles_store_errors
async def list_my_orders(tool_context: ToolContext) -> dict:
    """Returns the customer's 10 most recent orders, with items and status."""
    rows = await _customer_rows("list-orders", tool_context)
    if not rows and not await _has_account(tool_context):
        return NO_ACCOUNT
    return {"status": "ok", "orders": rows}


@_handles_store_errors
async def change_delivery_method(
    order_id: int, delivery_method: str, tool_context: ToolContext
) -> dict:
    """Changes the delivery method of one of the customer's pending orders.

    The customer is asked to confirm before this runs.

    Args:
        order_id: The order number.
        delivery_method: The new method, offered by the order's store.
    """
    (row,) = await _customer_rows(
        "change-delivery-method",
        tool_context,
        order_id=order_id,
        delivery_method=delivery_method,
    )
    if row["order_id"]:
        return {
            "status": "ok",
            "order_id": row["order_id"],
            "delivery_method": delivery_method,
            "shipping_cost": row["shipping_cost"],
            "total_amount": row["total_amount"],
        }
    if not row["pending_order_found"]:
        # Same answer whether the order is someone else's or not pending, so
        # other customers' order numbers reveal nothing.
        return {
            "status": "error",
            "message": f"You have no pending order #{order_id}. Only pending orders "
            "can be changed.",
        }
    return {
        "status": "error",
        "message": f"The order's store doesn't offer {delivery_method}.",
    }


@_handles_store_errors
async def cancel_order(order_id: int, tool_context: ToolContext) -> dict:
    """Cancels one of the customer's pending orders; its items go back in stock.

    The customer is asked to confirm before this runs.

    Args:
        order_id: The order number.
    """
    (row,) = await _customer_rows("cancel-order", tool_context, order_id=order_id)
    if row["cancelled_order_id"]:
        return {
            "status": "ok",
            "cancelled_order_id": row["cancelled_order_id"],
            "refunded_amount": row["refunded_amount"],
        }
    return {
        "status": "error",
        "message": f"You have no pending order #{order_id}. Only pending orders "
        "can be cancelled.",
    }


# Reads run freely; changes to an order need the customer's confirmation.
customer_tools = [
    get_my_profile,
    find_nearest_stores,
    add_to_cart,
    view_cart,
    remove_from_cart,
    FunctionTool(place_order, require_confirmation=True),
    list_my_orders,
    FunctionTool(change_delivery_method, require_confirmation=True),
    FunctionTool(cancel_order, require_confirmation=True),
]
