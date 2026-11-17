#!/bin/bash
# Development only: the demo MySQL database for "Connect a database" (docs/SETUP_DATABASES.md).
# Run by the MySQL image's entrypoint while its first-start server listens on the socket only. Loads the UrbanCart demo files
# (make fixtures) into orders, customers and products, adds a monthly view and creates a read-only user analyst_ro.
# demo_owner (MYSQL_USER) can write: connecting with it shows the warning. Demo passwords, not secrets.
set -e

sql() {
  mysql --protocol=socket -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"
}

sql <<'SQL'
CREATE TABLE orders (
  id BIGINT PRIMARY KEY, name VARCHAR(32), order_number INT, created_at DATETIME, updated_at DATETIME,
  customer_id BIGINT, sales_channel VARCHAR(32), financial_status VARCHAR(32), fulfillment_status VARCHAR(32),
  payment_gateway VARCHAR(32), product_id BIGINT, product_title VARCHAR(255), product_type VARCHAR(64),
  sku VARCHAR(64), quantity INT, unit_price DECIMAL(12, 2), total_line_items_price DECIMAL(12, 2),
  total_discounts DECIMAL(12, 2), total_price DECIMAL(12, 2), total_refunds DECIMAL(12, 2), currency CHAR(3),
  shipping_city VARCHAR(64), shipping_state VARCHAR(64), shipping_region VARCHAR(16)
);
CREATE TABLE customers (
  id BIGINT PRIMARY KEY, created_at DATETIME, city VARCHAR(64), state VARCHAR(64), region VARCHAR(16),
  segment VARCHAR(32), acquisition_channel VARCHAR(32), accepts_marketing TINYINT(1), orders_count INT,
  total_spent DECIMAL(12, 2)
);
CREATE TABLE products (
  id BIGINT PRIMARY KEY, title VARCHAR(255), product_type VARCHAR(64), sku VARCHAR(64), vendor VARCHAR(64),
  price DECIMAL(12, 2), cost_per_item DECIMAL(12, 2), status VARCHAR(16), created_at DATETIME,
  inventory_quantity INT
);
SQL

if [ -f /fixtures/shopify_orders.csv ]; then
  sql <<'SQL'
LOAD DATA INFILE '/fixtures/shopify_orders.csv' INTO TABLE orders
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES
  (id, name, order_number, @created, @updated, customer_id, sales_channel, financial_status, fulfillment_status,
   payment_gateway, product_id, product_title, product_type, sku, quantity, unit_price, total_line_items_price,
   total_discounts, total_price, total_refunds, currency, shipping_city, shipping_state, shipping_region)
  SET created_at = STR_TO_DATE(LEFT(@created, 19), '%Y-%m-%dT%H:%i:%s'),
      updated_at = STR_TO_DATE(LEFT(@updated, 19), '%Y-%m-%dT%H:%i:%s');
LOAD DATA INFILE '/fixtures/shopify_customers.csv' INTO TABLE customers
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES
  (id, @first_name, @last_name, @email, @created, city, state, region, segment, acquisition_channel, @marketing,
   orders_count, total_spent)
  SET created_at = STR_TO_DATE(LEFT(@created, 19), '%Y-%m-%dT%H:%i:%s'),
      accepts_marketing = (@marketing = 'True');
LOAD DATA INFILE '/fixtures/shopify_products.csv' INTO TABLE products
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 LINES
  (id, title, product_type, sku, vendor, price, cost_per_item, status, @created, inventory_quantity)
  SET created_at = STR_TO_DATE(LEFT(@created, 19), '%Y-%m-%dT%H:%i:%s');
SQL
else
  echo "demo-mysql: eval/fixtures not found (run make fixtures); the tables stay empty"
fi

sql <<'SQL'
CREATE VIEW monthly_sales AS
  SELECT DATE_FORMAT(created_at, '%Y-%m-01') AS month, shipping_region AS region,
         SUM(total_price - total_refunds) AS net_revenue, COUNT(*) AS orders
  FROM orders GROUP BY 1, 2;
CREATE USER 'analyst_ro'@'%' IDENTIFIED BY 'demo-readonly';
GRANT SELECT ON urbancart_demo.* TO 'analyst_ro'@'%';
SQL
