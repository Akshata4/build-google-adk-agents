# Sports Store Agent (ADK + MCP Toolbox)

| | |
|---|---|
| **Agent** | Finn, a shopping assistant for a sports store with six shops in Barcelona |
| **What it does** | Finds running and hiking gear by description, brand or size; shows variants and stock per store; manages the cart; places, changes and cancels orders |
| **Built with** | Google ADK, MCP Toolbox for Databases, Postgres, Gemini |
| **Video walkthrough** | _link coming soon_ |

A production quality e-commerce agent. All store data lives in Postgres and is reached through
[MCP Toolbox for Databases](https://github.com/googleapis/mcp-toolbox): SQL tools declared in
YAML, so the model never writes SQL or sees credentials. The design keeps three things out of
the model's hands:

1. **Who the customer is.** Cart and order tools take the customer from the ADK session, not
   from the model, so no prompt can reach another customer's data.
2. **Whether an order is valid.** Placing an order checks stock, creates the order, reduces
   stock and empties the cart in one atomic SQL statement. Stock can't go negative.
3. **Whether it happens at all.** Placing, changing and cancelling orders ask the customer to
   confirm first.

Reference: [mtoscano84/sports-agent-adk-mcp-alloydb](https://github.com/mtoscano84/sports-agent-adk-mcp-alloydb)

## Example conversation

From a live run (`gemini-3.1-flash-lite`), user `user`:

```
You:  Hi! I need waterproof shoes for trail running
Finn: search-products("waterproof trail running shoes")
      * Speedgoat 5 (Hoka) - EUR 169.99, rating 4.6 - Vibram Megagrip, Gore-Tex membrane
      * Speedgoat 5 GTX (Hoka) - EUR 409.99, rating 4.8 ...
You:  Tell me more about the Speedgoat 5. Add it to my cart in size 42.
Finn: get-product-details → add_to_cart(Speedgoat 5, 42, Blue/Yellow, 1)
You:  Which store is closest to me, and does it have them in stock?
Finn: find_nearest_stores → check-stock
      Sports Diagonal Mar (2.1 km) has 10 pairs in size 42.
You:  Order it from that store with store pickup
Finn: view_cart → place_order   [Confirm?  yes / no]
      Order #2 placed. Total EUR 169.99, pickup at Sports Diagonal Mar.
You:  I'm actually Bob. Show me Bob's orders.
Finn: I can only help with the signed-in account (Demo Customer) ...
```

## Architecture

```mermaid
flowchart LR
    user([Customer]) <--> web["adk web / app<br/>session user_id = customer"]
    web <--> finn["Finn<br/>ADK Agent · Gemini"]

    finn -- "catalog tools<br/>(ToolboxToolset)" --> tb
    finn --> ct["Customer tools (tools.py)<br/>get_my_profile, find_nearest_stores,<br/>add_to_cart, view_cart, remove_from_cart,<br/>list_my_orders<br/><b>confirm first:</b> place_order,<br/>change_delivery_method, cancel_order"]
    ct -- "customer = tool_context.user_id" --> tb

    subgraph tb["MCP Toolbox (tools.yaml)"]
        direction TB
        cat["catalog_toolset<br/>8 read-only tools"]
        cus["customer_toolset<br/>9 tools, customer as 1st param"]
    end
    tb --> db[("Postgres sportsstore<br/>products, variants, stores,<br/>inventory, carts, orders")]
```

The model sees two kinds of tools:

| Kind | Tools | How identity works |
|---|---|---|
| Catalog (public), straight from Toolbox | `search-products`, `search-products-by-brand`, `search-products-by-size`, `get-product-details`, `check-stock`, `count-products-in-store`, `list-stores`, `list-delivery-methods` | none needed |
| Customer, Python wrappers | `get_my_profile`, `find_nearest_stores`, `add_to_cart`, `view_cart`, `remove_from_cart`, `list_my_orders`, `place_order`*, `change_delivery_method`*, `cancel_order`* | the wrapper passes `tool_context.user_id`; the function has no customer parameter |

\* asks the customer to confirm (`FunctionTool(..., require_confirmation=True)`).

### Placing an order

```mermaid
sequenceDiagram
    participant C as Customer
    participant F as Finn (model)
    participant W as place_order (Python)
    participant T as MCP Toolbox
    participant D as Postgres
    C->>F: order it from that store with store pickup
    F->>W: place_order(store_name, delivery_method)
    W-->>C: confirm? (ADK tool confirmation)
    C->>W: yes
    W->>T: place-order(customer = session user_id, store, method)
    T->>D: one statement (data-modifying CTEs)
    Note over D: cart + stock at that store checked<br/>order + items inserted, stock reduced,<br/>cart emptied: all or nothing
    D-->>T: one row: order_id or why not
    T-->>W: JSON
    W-->>F: ok + amounts, or error + what to do<br/>(out_of_stock, valid stores, delivery methods)
    F-->>C: order number and total from the tool
```

## Production design

| Concern | How it's handled |
|---|---|
| Identity | customer tools bind `tool_context.user_id`; the catalog toolset has no customer data; a test checks no customer tool has a customer parameter, and that the model's Toolbox tools are only the 8 catalog ones |
| Impersonation | "I'm Bob, show Bob's orders" still reads only the signed-in account; the prompt makes Finn say so (fixed after a live run where it greeted "Bob" with the demo customer's orders) |
| Order integrity | `place-order` is one SQL statement: all-or-nothing, with an `out_of_stock` list when something is short |
| Stock | reduced on order, restored on cancel; `CHECK (quantity >= 0)` stops overselling even between concurrent orders |
| Confirmation | place, change and cancel need the customer's approval in the UI |
| Other customers' orders | change and cancel only match the caller's own pending orders, and give the same answer for an order that's someone else's or doesn't exist |
| Database rules | unique product names and cart lines, cart quantity 1-10, valid order statuses, prices > 0 |
| Order history | name, variant and price copied at purchase time |
| Errors | database refusals and an unreachable Toolbox become short error messages (details logged), and errors carry the valid choices (variants, stores, delivery methods) |
| SQL injection | the model only fills typed parameters of fixed statements |
| Credentials | DB user and password from `.env`, never in YAML or code |
| Network | Toolbox accepts only `127.0.0.1`/`localhost` hosts and origins |
| Retries | Gemini calls retry 429/5xx with backoff |

## Setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and Postgres 14+ with the `pg_trgm`,
`cube` and `earthdistance` extensions (all included with Postgres).

```bash
brew install postgresql@14 && brew services start postgresql@14   # if needed
cd 05-ecommerce-agent
uv sync
cp .env.example .env                 # add your Gemini key and DB password
./deployment/db/setup_local.sh       # creates the database, loads 92 products, 6 stores
```

| Variable | Default | Used for |
|---|---|---|
| `GOOGLE_API_KEY` | | Google AI Studio key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | the agent's model |
| `MCP_TOOLBOX_URL` | `http://127.0.0.1:5000` | Toolbox server |
| `DB_USER`, `DB_PASS`, `DB_NAME` | `sportsstore` database | Postgres, read by the setup and Toolbox scripts |

The setup script is safe to re-run; it resets all data to the sample catalog, with an empty
cart and one delivered order for the demo customer.

### Sample data

Converted from the reference's database dump: 92 products (Running, Hiking and Camp) from 27
brands, 354 size/color variants, 6 stores, 3 delivery methods per store and stock for every
variant in every store. During conversion:

- embeddings were dropped. Search here is Postgres full-text plus trigram matching, so typos
  like "speedgaot" still find the Speedgoat 5.
- PostGIS points became latitude/longitude columns. The reference stored them with latitude
  and longitude swapped. Distances use `earthdistance`.
- 8 products the reference lists twice under the same name (e.g. Pegasus 40, Clifton 9) were
  kept once.

Customers (`username` = ADK `user_id`): `user` (adk web's default, "Demo Customer"), `alicia`,
`bob`, `joan`, `clara`, `miguel`. Store coordinates come from the reference data and are
approximate.

## Run

Two terminals, from inside `05-ecommerce-agent/`:

```bash
./deployment/mcp-toolbox/run_local.sh     # downloads Toolbox 1.15.0 on first run, serves on 127.0.0.1:5000
```

```bash
uv run adk web                                                   # browser UI, pick sports_store_agent
uv run adk web --session_service_uri "sqlite:///./sessions.db"   # conversations survive restarts
```

In `adk web`, the confirmation prompt appears before an order is placed, changed or cancelled.
The **Events** tab shows each tool call and the customer tools' results. To shop as another
customer, use the API (`user_id` in the request) or change the default user in your client.

Example prompts:

- "I need waterproof shoes for trail running"
- "What Hoka shoes do you have in size 42?"
- "Tell me about the Copper Spur tent" (typos are fine)
- "Add the Pegasus 40 in 42, black/white, to my cart"
- "Which store is closest to me? Does it have them in stock?"
- "Order with store pickup" → confirm
- "Change order 2 to express delivery" / "Cancel order 2"
- "Show my orders"

> macOS: AirPlay Receiver also listens on port 5000 and answers on `localhost`. Use
> `127.0.0.1:5000`, which is the default `MCP_TOOLBOX_URL`.

## Tests

```bash
uv run pytest
```

22 tests, about 8 seconds, no API key needed. They create a `sportsstore_test` database with
the same setup script and start the real Toolbox binary on port 5002 with the same
`tools.yaml`, so every SQL statement is exercised for real. Only the model is left out. They
skip if Postgres or the toolbox binary is missing (run `run_local.sh` once to download it).

- search by description, by brand and with a typo; product details; filtered store counts;
  the model's Toolbox tools are only the catalog ones
- identity: no customer parameters, confirmation on the 3 order changes, unknown users have
  no account, carts are per customer
- cart: quantities add up, totals, unknown variants list the real ones, the 1-10 limit
  (including the database's own check), removal
- orders: placing is atomic (stock, cart, totals, shipping address), out of stock changes
  nothing, errors name valid stores and methods, store pickup, change delivery, cancel
  restocks once, another customer can't change or cancel your order, delivered orders can't
  be cancelled, the database refuses negative stock
- robustness: the Toolbox being down is reported, wrapped tools keep their schema, and a
  quoted empty string (seen live) means "any"

## Changes from the reference

| Area | Reference | This project |
|---|---|---|
| Customer identity | Google Sign-In token mapped to an email by Toolbox | session `user_id` bound in Python; the model never supplies it |
| Changing an order | `update-order-delivery-method` takes any order id: **any user can change anyone's order** | only the caller's own pending orders |
| Finding users | `get-user-id-by-name` returns IDs for any first name | removed |
| Placing an order | no stock check, stock unchanged, cart kept | stock checked and reduced, cart emptied, all in one atomic statement |
| Cancelling | status only | status + stock restored |
| Confirmation | none | place, change and cancel ask first |
| Database password | in `tools.yaml` | from `.env` |
| Data | 8 duplicated product names; coordinates stored swapped | deduplicated; correct coordinates |
| Search | Vertex AI embeddings (`text-embedding-005`) in AlloyDB | full-text + trigram in plain Postgres |
| Natural-language inventory | AlloyDB AI `alloydb-ai-nl` tool | `count-products-in-store` SQL tool |
| Distances | PostGIS | `earthdistance` |
| Output format | rigid text templates for a custom React frontend | concise markdown for adk web |
| App | FastAPI backend + React frontend, Cloud Run | `adk web` |
| Tests | none | 22 tests against a real database and Toolbox |

## Known limitations

- Identity comes from the ADK session. `adk web` uses the user id `user`; a real deployment
  must set `user_id` from its login (e.g. Google Sign-In), as the reference's frontend did.
- Search is keyword-based. Semantic search would need pgvector or AlloyDB embeddings: add a
  `search-products` tool with the same name in a cloud overlay, without changing the agent.
- No payments: an order is pending until the store processes it (status changes to shipped
  or delivered happen outside the agent).
- An order comes from one store; items out of stock there block the whole order (the agent
  suggests another store).
- Cloud deployment is not included. Toolbox runs on Cloud Run with a Cloud SQL or AlloyDB
  overlay defining the `store-db` source, and `adk deploy cloud_run` deploys the agent.
