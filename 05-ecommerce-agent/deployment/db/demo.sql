-- Order history for the demo customer ("user", adk web's default user id):
-- one delivered order, so "show my orders" has something to show.
WITH o AS (
    INSERT INTO orders (customer_id, store_id, delivery_method_id, status,
                        shipping_address, items_total, shipping_cost, total_amount, created_at)
    SELECT c.customer_id, 2, d.delivery_method_id, 'delivered',
           c.address || ', ' || c.postal_code || ' ' || c.city, 129.99, d.cost, 129.99 + d.cost,
           now() - interval '20 days'
    FROM customers c
    JOIN delivery_methods d ON d.store_id = 2 AND d.name = 'Standard Home Delivery'
    WHERE c.username = 'user'
    RETURNING order_id
)
INSERT INTO order_items (order_id, variant_id, quantity, unit_price, product_name, variant_details)
SELECT o.order_id, v.variant_id, 1, v.price, p.name, 'Size: ' || v.size || ', Color: ' || v.color
FROM o, product_variants v JOIN products p USING (product_id)
WHERE p.name = 'Pegasus 40' AND v.size = '42' AND v.color = 'Black/White';
