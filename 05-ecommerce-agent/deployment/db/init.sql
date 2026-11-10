-- Schema for the sports store. Safe to re-run: drops and recreates everything.
-- Rules live in the database, so no tool or model can break them:
-- stock never goes negative, cart quantities are 1-10, one cart line per
-- variant, and order statuses come from a fixed list.

CREATE EXTENSION IF NOT EXISTS pg_trgm;        -- typo-tolerant name matching
CREATE EXTENSION IF NOT EXISTS cube;           -- needed by earthdistance
CREATE EXTENSION IF NOT EXISTS earthdistance;  -- store distances in metres

DROP TABLE IF EXISTS order_items, orders, cart_items, inventory, delivery_methods,
    customers, stores, product_variants, products CASCADE;

CREATE TABLE products (
    product_id   serial PRIMARY KEY,
    name         text NOT NULL,
    description  text,
    category     text NOT NULL,
    brand        text NOT NULL,
    image_url    text,
    rating       numeric(2, 1),
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', name), 'A') ||
        setweight(to_tsvector('english', brand), 'A') ||
        setweight(to_tsvector('english', category), 'B') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'C')
    ) STORED
);
CREATE UNIQUE INDEX products_name_key ON products (lower(name));
CREATE INDEX products_search_idx ON products USING gin (search_vector);
CREATE INDEX products_name_trgm_idx ON products USING gin ((name || ' ' || brand) gin_trgm_ops);

CREATE TABLE product_variants (
    variant_id  serial PRIMARY KEY,
    product_id  integer NOT NULL REFERENCES products,
    size        text NOT NULL,
    color       text NOT NULL,
    price       numeric(10, 2) NOT NULL CHECK (price > 0),
    UNIQUE (product_id, size, color)
);

CREATE TABLE stores (
    store_id    serial PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    address     text NOT NULL,
    city        text NOT NULL,
    postal_code text,
    latitude    double precision NOT NULL,
    longitude   double precision NOT NULL,
    rating      numeric(2, 1)
);

CREATE TABLE delivery_methods (
    delivery_method_id serial PRIMARY KEY,
    store_id       integer NOT NULL REFERENCES stores,
    name           text NOT NULL,
    description    text,
    cost           numeric(10, 2) NOT NULL CHECK (cost >= 0),
    estimated_time text,
    UNIQUE (store_id, name)
);

CREATE TABLE inventory (
    variant_id integer NOT NULL REFERENCES product_variants,
    store_id   integer NOT NULL REFERENCES stores,
    quantity   integer NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (variant_id, store_id)
);

-- username is the ADK session's user_id. In production it would come from the
-- app's login (e.g. Google Sign-In), never from the model.
CREATE TABLE customers (
    customer_id serial PRIMARY KEY,
    username    text NOT NULL UNIQUE,
    first_name  text NOT NULL,
    last_name   text NOT NULL,
    address     text NOT NULL,
    city        text NOT NULL,
    postal_code text,
    latitude    double precision NOT NULL,
    longitude   double precision NOT NULL
);

CREATE TABLE cart_items (
    customer_id integer NOT NULL REFERENCES customers,
    variant_id  integer NOT NULL REFERENCES product_variants,
    quantity    integer NOT NULL CHECK (quantity BETWEEN 1 AND 10),
    added_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (customer_id, variant_id)
);

CREATE TABLE orders (
    order_id           serial PRIMARY KEY,
    customer_id        integer NOT NULL REFERENCES customers,
    store_id           integer NOT NULL REFERENCES stores,
    delivery_method_id integer NOT NULL REFERENCES delivery_methods,
    status             text NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled')),
    shipping_address   text,  -- NULL for store pickup
    items_total        numeric(12, 2) NOT NULL,
    shipping_cost      numeric(10, 2) NOT NULL,
    total_amount       numeric(12, 2) NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX orders_customer_idx ON orders (customer_id, created_at DESC);

-- Name, details and price are copied at purchase time, so later catalog
-- changes don't rewrite order history.
CREATE TABLE order_items (
    order_id        integer NOT NULL REFERENCES orders,
    variant_id      integer NOT NULL REFERENCES product_variants,
    quantity        integer NOT NULL CHECK (quantity > 0),
    unit_price      numeric(10, 2) NOT NULL,
    product_name    text NOT NULL,
    variant_details text NOT NULL,
    PRIMARY KEY (order_id, variant_id)
);
