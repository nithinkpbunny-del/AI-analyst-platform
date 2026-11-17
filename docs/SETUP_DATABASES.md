# Connecting a PostgreSQL or MySQL database

The app can read your own database: you choose which tables and views questions may use, and the app reads them
(in chunks, then only changed rows when a table has an `updated_at`-style column) or, for big tables, asks the
database itself with one read-only query (push-down). It **never changes anything**, in several layers:

1. **A read-only user** (this guide): create a database user that can only `SELECT`. When you connect, the app
   checks what the user could do and warns you if it can change data.
2. **Read-only sessions:** every session the app opens is read-only (PostgreSQL:
   `default_transaction_read_only=on` and `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`; MySQL:
   `SET SESSION TRANSACTION READ ONLY` and `START TRANSACTION READ ONLY`), with a statement timeout (30 seconds;
   long analyses 120 seconds). Nothing is ever committed.
3. **SELECT only:** every query is parsed first; only a single `SELECT` on the tables you chose passes. Writes,
   several statements, `FOR UPDATE`, `SELECT ... INTO`, `COPY`, `SET`, sleeping, file and network functions and
   other databases are refused.
4. **Row limits:** a table is read up to 1,000,000 rows (`MAX_ROWS_PER_DATASET`); a push-down answer returns at most
   200 rows to the AI.

Also:

- **Only public addresses.** The host must be reachable from the internet. Private and internal addresses are
  refused: `localhost`, `127.x`, `10.x`, `172.16-31.x` (also the Docker network), `192.168.x`, `169.254.x` (incl.
  cloud metadata), `100.64-127.x`, and their IPv6 equivalents, as well as names ending in `.local`, `.internal` or
  `.localhost`. Open your firewall to this platform's outgoing address only.
- **SSL is required.** Choose **Encrypted, certificate checked** when your server has a certificate from a public
  authority (most cloud databases), or **Encrypted, certificate not checked** for a self-signed certificate.
  Unencrypted connections exist only in development.
- **The password is stored encrypted** and never shown again; the user name is shown masked (`an••••`).
- **SSH tunnels are not supported yet.** Use a firewall rule (allow-list) instead.

## Part A: trying it with the demo databases (development only)

`make dev` also starts two demo databases with the UrbanCart demo data (run `make fixtures` once first, or their
tables stay empty). They are reachable from the app as `demo-postgres` and `demo-mysql`, which
`DB_DEV_ALLOWED_HOSTS` allows **in development only**; from your computer they are on `127.0.0.1:5433` and
`127.0.0.1:3307`.

| | PostgreSQL | MySQL |
|---|---|---|
| Host (in the app) | `demo-postgres` | `demo-mysql` |
| Port | `5432` (leave empty) | `3306` (leave empty) |
| Database | `urbancart_demo` | `urbancart_demo` |
| Read-only user / password | `analyst_ro` / `demo-readonly` | `analyst_ro` / `demo-readonly` |
| A user who can write | `demo_owner` / `demo-owner-pass` | `demo_owner` / `demo-owner-pass` |
| Encryption | **Not encrypted (development only)** | **Encrypted, certificate not checked** |
| Tables | `orders` (28,540), `customers` (3,000, no names or e-mails), `products` (40), view `monthly_sales` | same |

These are demo passwords for local containers, not secrets.

1. Open **Connections > Add data > Connect a database**.
2. Fill in the PostgreSQL column above and press **Check and connect**. You land on the new connection.
3. Under **Tables in the database**, tick `orders` and `monthly_sales` and press **Save tables**. The tables are
   read in the background (a few seconds); `orders` has an `updated_at` column, so later reads ask only for
   changed rows.
4. Try `demo_owner` instead (Replace credentials): the page warns that this user can change data. The app still
   only reads.
5. Try host `localhost` or `10.0.0.5`: the app refuses private addresses (the demo names are the only exception,
   and only in development).
6. Repeat with the MySQL column.

## Part B: creating a read-only user on your database

### PostgreSQL

Run as an administrator, replacing the names and the password:

```sql
CREATE ROLE analyst_ro LOGIN PASSWORD 'a-long-random-password';
GRANT CONNECT ON DATABASE your_database TO analyst_ro;
GRANT USAGE ON SCHEMA public TO analyst_ro;
-- Only the tables the app may read (or ALL TABLES IN SCHEMA public):
GRANT SELECT ON public.orders, public.customers TO analyst_ro;
-- Make sure it cannot create tables (PostgreSQL 14 and older allow this by default):
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

On a managed database (Amazon RDS, Google Cloud SQL, Azure, Supabase, Neon...), turn on SSL (usually on already),
and allow connections from this platform's outgoing IP address. If you have a **read replica**, connect the app to
the replica.

### MySQL / MariaDB

```sql
CREATE USER 'analyst_ro'@'%' IDENTIFIED BY 'a-long-random-password' REQUIRE SSL;
GRANT SELECT ON your_database.* TO 'analyst_ro'@'%';
```

Replace `'%'` with this platform's outgoing IP address if you can.

## What the connection check shows

- **Signed in to the source:** the user and password work.
- **Read-only access:** the user cannot change rows, create tables or manage users. If it can, the connection still
  works (the sessions are read-only), but the page shows a warning until you enter a read-only user with
  **Replace credentials**.
- **Tables can be listed:** the user can read at least one table.

## Settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `DB_DEV_ALLOWED_HOSTS` | `demo-postgres,demo-mysql` | Development only: host names that may be private addresses (the demo databases). Ignored outside development. |
| `DB_CONNECT_TIMEOUT_SECONDS` | `10` | How long to wait for the database to answer a connection. |
| `DB_FETCH_CHUNK_ROWS` | `10000` | Rows fetched per round trip while a table is read. |
| `SOURCE_QUERY_TIMEOUT_SECONDS` | `30` | Statement timeout (long analyses: `SOURCE_QUERY_TIMEOUT_LONG_TASK_SECONDS`, 120). |
| `MAX_ROWS_PER_DATASET` | `1000000` | Rows read per table at most. |

## Not supported yet

- SSH tunnels (BUILD_SPEC 8.7 "optional SSH tunnel"); use a firewall allow-list.
- Other databases (SQL Server, Oracle, Snowflake...).
- Binary and spatial columns (`bytea`, `blob`, `geometry`...) are not read.
- Tables or schemas whose names contain quotes or backslashes.
