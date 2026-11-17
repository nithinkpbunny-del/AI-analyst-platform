#!/bin/sh
# Development only: the demo PostgreSQL database for "Connect a database" (docs/SETUP_DATABASES.md).
# Loads the UrbanCart demo files (make fixtures) into orders, customers and products, adds a monthly view, and
# creates a read-only user analyst_ro. The image's own user (demo_owner) can write: connecting with it shows the
# "this user can change data" warning. These are demo passwords for a local container, not secrets.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE TABLE orders (
  id bigint PRIMARY KEY, name text, order_number integer, created_at timestamptz, updated_at timestamptz,
  customer_id bigint, sales_channel text, financial_status text, fulfillment_status text, payment_gateway text,
  product_id bigint, product_title text, product_type text, sku text, quantity integer, unit_price numeric(12, 2),
  total_line_items_price numeric(12, 2), total_discounts numeric(12, 2), total_price numeric(12, 2),
  total_refunds numeric(12, 2), currency text, shipping_city text, shipping_state text, shipping_region text
);
CREATE TABLE customers (
  id bigint PRIMARY KEY, created_at timestamptz, city text, state text, region text, segment text,
  acquisition_channel text, accepts_marketing boolean, orders_count integer, total_spent numeric(12, 2)
);
CREATE TABLE products (
  id bigint PRIMARY KEY, title text, product_type text, sku text, vendor text, price numeric(12, 2),
  cost_per_item numeric(12, 2), status text, created_at timestamptz, inventory_quantity integer
);
SQL

if [ -f /fixtures/shopify_orders.csv ]; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\copy orders FROM '/fixtures/shopify_orders.csv' WITH (FORMAT csv, HEADER true)
CREATE TEMP TABLE staged_customers (
  id bigint, first_name text, last_name text, email text, created_at timestamptz, city text, state text,
  region text, segment text, acquisition_channel text, accepts_marketing boolean, orders_count integer,
  total_spent numeric(12, 2)
);
\copy staged_customers FROM '/fixtures/shopify_customers.csv' WITH (FORMAT csv, HEADER true)
-- Names and e-mail addresses stay out of the demo database.
INSERT INTO customers SELECT id, created_at, city, state, region, segment, acquisition_channel, accepts_marketing,
  orders_count, total_spent FROM staged_customers;
\copy products FROM '/fixtures/shopify_products.csv' WITH (FORMAT csv, HEADER true)
SQL
else
  echo "demo-postgres: eval/fixtures not found (run make fixtures); the tables stay empty"
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE VIEW monthly_sales AS
  SELECT date_trunc('month', created_at)::date AS month, shipping_region AS region,
         SUM(total_price - total_refunds) AS net_revenue, COUNT(*) AS orders
  FROM orders GROUP BY 1, 2;
CREATE ROLE analyst_ro LOGIN PASSWORD 'demo-readonly';
GRANT CONNECT ON DATABASE urbancart_demo TO analyst_ro;
GRANT USAGE ON SCHEMA public TO analyst_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst_ro;
SQL
