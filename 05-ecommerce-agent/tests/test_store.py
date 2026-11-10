"""The agent's tools against the real database and Toolbox (no model).

Customer tools are called the way ADK calls them, with a ToolContext whose
user_id is the signed-in customer.
"""

import asyncio
import inspect
import subprocess
from types import SimpleNamespace

from conftest import psql
from google.adk.tools import FunctionTool
from google.adk.tools.toolbox_toolset import ToolboxToolset

from sports_store_agent import tools
from sports_store_agent.agent import root_agent

USER = SimpleNamespace(user_id="user")  # adk web's default user, a demo customer
BOB = SimpleNamespace(user_id="bob")
STRANGER = SimpleNamespace(user_id="stranger")  # no customer account


def run(coro):
    return asyncio.run(coro)


def stock(product, size, color, store):
    return int(
        psql(
            "SELECT i.quantity FROM inventory i JOIN product_variants v USING (variant_id) "
            "JOIN products p USING (product_id) JOIN stores s USING (store_id) "
            f"WHERE p.name = '{product}' AND v.size = '{size}' AND v.color = '{color}' "
            f"AND s.name = '{store}'"
        )
    )


def delivery_cost(store, method):
    return float(
        psql(
            "SELECT d.cost FROM delivery_methods d JOIN stores s USING (store_id) "
            f"WHERE s.name = '{store}' AND d.name = '{method}'"
        )
    )


def order_count():
    return int(psql("SELECT count(*) FROM orders"))


# ----- Catalog -----
def test_search_by_description_and_with_a_typo(toolbox):
    found = run(tools._run("search-products", query="waterproof trail running shoes"))
    assert found[0]["name"] == "Speedgoat 5"
    typo = run(tools._run("search-products", query="speedgaot"))
    assert {p["name"] for p in typo} >= {"Speedgoat 5", "Speedgoat 5 GTX"}
    assert run(tools._run("search-products", query="xyzzy plugh")) == []


def test_product_details_and_brand(toolbox):
    (details,) = run(tools._run("get-product-details", product_name="speedgoat 5"))
    assert details["name"] == "Speedgoat 5"  # exact match wins over "Speedgoat 5 GTX"
    assert {"size": "42", "color": "Blue/Yellow", "price": 169.99} in details[
        "variants"
    ]
    hoka = run(tools._run("search-products-by-brand", brand="hoka"))
    assert hoka and {p["brand"] for p in hoka} == {"Hoka"}


def test_store_inventory_count_respects_filters(toolbox):
    (all_items,) = run(
        tools._run(
            "count-products-in-store", store_name="Sports Gracia", brand="", category=""
        )
    )
    (hoka,) = run(
        tools._run(
            "count-products-in-store",
            store_name="sports gracia",
            brand="Hoka",
            category="",
        )
    )
    assert 0 < hoka["products_in_stock"] < all_items["products_in_stock"]
    assert hoka["units_in_stock"] < all_items["units_in_stock"]


def test_model_sees_only_catalog_tools_from_toolbox(toolbox):
    toolset = ToolboxToolset(toolbox, toolset_name="catalog_toolset")
    names = {t.name for t in run(toolset.get_tools())}
    assert names == {
        "search-products",
        "search-products-by-brand",
        "search-products-by-size",
        "get-product-details",
        "check-stock",
        "count-products-in-store",
        "list-stores",
        "list-delivery-methods",
    }


# ----- Identity -----
def test_customer_tools_take_no_customer_argument():
    for tool in tools.customer_tools:
        func = tool.func if isinstance(tool, FunctionTool) else tool
        params = set(inspect.signature(func).parameters) - {"tool_context"}
        assert not params & {"customer", "user", "user_id", "username", "email"}, func
    confirmed = {
        t.func.__name__
        for t in tools.customer_tools
        if isinstance(t, FunctionTool) and t._require_confirmation
    }
    assert confirmed == {"place_order", "change_delivery_method", "cancel_order"}
    assert root_agent.tools[1:] == tools.customer_tools


def test_unknown_user_has_no_account(store):
    assert run(tools.view_cart(STRANGER)) == tools.NO_ACCOUNT
    assert run(tools.list_my_orders(STRANGER)) == tools.NO_ACCOUNT
    assert run(tools.add_to_cart("Speedgoat 5", "42", "Blue/Yellow", 1, STRANGER)) == (
        tools.NO_ACCOUNT
    )
    assert run(tools.place_order("Sports Gracia", "Store Pickup", STRANGER)) == (
        tools.NO_ACCOUNT
    )


def test_profile_and_nearest_stores(toolbox):
    profile = run(tools.get_my_profile(USER))["profile"]
    assert profile["first_name"] == "Demo"
    stores = run(tools.find_nearest_stores(USER))["stores"]
    distances = [s["distance_m"] for s in stores]
    assert len(stores) == 3 and distances == sorted(distances)


# ----- Cart -----
def test_cart_adds_up_and_totals(store):
    run(tools.add_to_cart("Speedgoat 5", "42", "Blue/Yellow", 1, USER))
    again = run(tools.add_to_cart("speedgoat 5", "42", "blue/yellow", 2, USER))
    assert again["quantity_in_cart"] == 3
    run(tools.add_to_cart("Pegasus 40", "42", "Black/White", 1, USER))
    cart = run(tools.view_cart(USER))
    assert [(i["product"], i["quantity"]) for i in cart["items"]] == [
        ("Speedgoat 5", 3),
        ("Pegasus 40", 1),
    ]
    assert cart["cart_total"] == round(3 * 169.99 + 129.99, 2)
    # Bob's cart is separate.
    assert run(tools.view_cart(BOB))["items"] == []


def test_unknown_variant_lists_the_real_ones(store):
    result = run(tools.add_to_cart("Speedgoat 5", "42", "Pink", 1, USER))
    assert result["status"] == "error"
    assert {"size": "42", "color": "Blue/Yellow", "price": 169.99} in result[
        "available_variants"
    ]
    nothing = run(tools.add_to_cart("Flying Carpet", "M", "Red", 1, USER))
    assert nothing["available_variants"] == []


def test_quantity_limits(store):
    assert (
        run(tools.add_to_cart("Pegasus 40", "42", "Black/White", 0, USER))["status"]
        == "error"
    )
    assert (
        run(tools.add_to_cart("Pegasus 40", "42", "Black/White", 6, USER))["status"]
        == "ok"
    )
    over = run(tools.add_to_cart("Pegasus 40", "42", "Black/White", 6, USER))
    assert over == {
        "status": "error",
        "message": "A cart line can hold at most 10 units.",
    }
    assert run(tools.view_cart(USER))["items"][0]["quantity"] == 6


def test_remove_from_cart(store):
    run(tools.add_to_cart("Pegasus 40", "42", "Black/White", 1, USER))
    assert (
        run(tools.remove_from_cart("Pegasus 40", "42", "Black/White", USER))["status"]
        == "ok"
    )
    assert (
        run(tools.remove_from_cart("Pegasus 40", "42", "Black/White", USER))["status"]
        == "error"
    )


# ----- Orders -----
def test_place_order_is_atomic(store):
    before = stock("Speedgoat 5", "42", "Blue/Yellow", "Sports Gracia")
    run(tools.add_to_cart("Speedgoat 5", "42", "Blue/Yellow", 2, USER))
    order = run(tools.place_order("Sports Gracia", "Express Delivery", USER))
    assert order["status"] == "ok"
    assert order["items_total"] == round(2 * 169.99, 2)
    express = delivery_cost("Sports Gracia", "Express Delivery")
    assert order["total_amount"] == round(2 * 169.99 + express, 2)
    assert order["shipping_address"] == "Carrer de Mallorca 401, 08013 Barcelona"
    assert stock("Speedgoat 5", "42", "Blue/Yellow", "Sports Gracia") == before - 2
    assert run(tools.view_cart(USER))["items"] == []
    latest = run(tools.list_my_orders(USER))["orders"][0]
    assert (latest["order_id"], latest["status"]) == (order["order_id"], "pending")
    assert latest["items"][0]["quantity"] == 2


def test_out_of_stock_changes_nothing(store):
    # Size 41 of Speedgoat 5 has no stock at Sports Gracia in the sample data.
    assert stock("Speedgoat 5", "41", "Blue/Yellow", "Sports Gracia") == 0
    run(tools.add_to_cart("Pegasus 40", "42", "Black/White", 1, USER))
    run(tools.add_to_cart("Speedgoat 5", "41", "Blue/Yellow", 1, USER))
    orders, pegasus = (
        order_count(),
        stock("Pegasus 40", "42", "Black/White", "Sports Gracia"),
    )
    result = run(tools.place_order("Sports Gracia", "Store Pickup", USER))
    assert result["status"] == "error"
    assert result["out_of_stock"] == [
        {
            "product": "Speedgoat 5",
            "details": "Size: 41, Color: Blue/Yellow",
            "requested": 1,
            "in_stock": 0,
        }
    ]
    assert order_count() == orders
    assert stock("Pegasus 40", "42", "Black/White", "Sports Gracia") == pegasus
    assert len(run(tools.view_cart(USER))["items"]) == 2


def test_order_errors_name_the_valid_choices(store):
    assert run(tools.place_order("Sports Gracia", "Store Pickup", USER))["message"] == (
        "The cart is empty."
    )
    run(tools.add_to_cart("Pegasus 40", "42", "Black/White", 1, USER))
    no_store = run(tools.place_order("Sports Moon", "Store Pickup", USER))
    assert "Sports Gracia" in no_store["stores"]
    no_method = run(tools.place_order("Sports Gracia", "Drone", USER))
    assert set(no_method["delivery_methods"]) == {
        "Store Pickup",
        "Standard Home Delivery",
        "Express Delivery",
    }


def test_store_pickup_has_no_shipping(store):
    run(tools.add_to_cart("Pegasus 40", "42", "Black/White", 1, USER))
    order = run(tools.place_order("sports gracia", "store pickup", USER))
    assert order["shipping_cost"] == 0
    assert order["shipping_address"] == "pickup at sports gracia"


def test_change_delivery_and_cancel(store):
    run(tools.add_to_cart("Pegasus 40", "42", "Black/White", 2, USER))
    before = stock("Pegasus 40", "42", "Black/White", "Sports Gracia")
    order_id = run(tools.place_order("Sports Gracia", "Store Pickup", USER))["order_id"]
    changed = run(tools.change_delivery_method(order_id, "Express Delivery", USER))
    express = delivery_cost("Sports Gracia", "Express Delivery")
    assert (changed["shipping_cost"], changed["total_amount"]) == (
        express,
        round(2 * 129.99 + express, 2),
    )
    assert (
        psql(
            f"SELECT shipping_address IS NOT NULL FROM orders WHERE order_id = {order_id}"
        )
        == "t"
    )

    cancelled = run(tools.cancel_order(order_id, USER))
    assert cancelled["refunded_amount"] == changed["total_amount"]
    assert (
        stock("Pegasus 40", "42", "Black/White", "Sports Gracia") == before
    )  # restocked
    assert run(tools.cancel_order(order_id, USER))["status"] == "error"  # only once
    assert (
        run(tools.change_delivery_method(order_id, "Store Pickup", USER))["status"]
        == "error"
    )


def test_other_customers_cannot_touch_my_order(store):
    run(tools.add_to_cart("Pegasus 40", "42", "Black/White", 1, USER))
    order_id = run(tools.place_order("Sports Gracia", "Store Pickup", USER))["order_id"]
    for attempt in (
        tools.cancel_order(order_id, BOB),
        tools.change_delivery_method(order_id, "Express Delivery", BOB),
    ):
        result = run(attempt)
        assert result["message"].startswith(f"You have no pending order #{order_id}")
    assert psql(
        f"SELECT status, shipping_cost FROM orders WHERE order_id = {order_id}"
    ) == ("pending|0.00")
    assert run(tools.list_my_orders(BOB))["orders"] == []


def test_delivered_orders_cannot_be_cancelled(toolbox):
    (delivered,) = run(tools.list_my_orders(USER))["orders"][-1:]
    assert delivered["status"] == "delivered"
    assert run(tools.cancel_order(delivered["order_id"], USER))["status"] == "error"


def test_database_refuses_negative_stock(toolbox):
    out = subprocess.run(
        [
            "psql",
            "-d",
            "sportsstore_test",
            "-c",
            "UPDATE inventory SET quantity = -1 WHERE variant_id = 1 AND store_id = 1",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "inventory_quantity_check" in out.stderr


def test_store_system_down_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(tools, "TOOLBOX_URL", "http://127.0.0.1:9")  # nothing listens
    result = run(tools.view_cart(USER))
    assert result["status"] == "error"
    assert "unavailable" in result["message"]


def test_wrapped_tools_keep_their_signature():
    # ADK builds the model's tool schema from the signature and docstring,
    # through the error-handling wrapper.
    declaration = FunctionTool(tools.add_to_cart)._get_declaration()
    schema = declaration.parameters_json_schema
    assert schema["required"] == ["product_name", "size", "color", "quantity"]
    assert schema["properties"]["quantity"]["type"] == "integer"
    assert declaration.description.startswith("Adds a product")


def test_quoted_empty_string_means_any(toolbox):
    # Seen live: the model passed '""' (two quote characters) for "every store".
    every = run(tools._run("check-stock", product_name="Speedgoat 5", store_name='""'))
    assert len({row["store"] for row in every}) == 6
    (one,) = run(
        tools._run(
            "count-products-in-store",
            store_name="Sports Gracia",
            brand='""',
            category="",
        )
    )
    assert one["brand"] is None and one["products_in_stock"] > 0
    sized = run(tools._run("search-products-by-size", size="42", category=' "" '))
    assert sized
