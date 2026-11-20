# Setting up Tally (M16)

Tally runs on the customer's Windows PC and has no cloud API. A small helper, the **Tally bridge**, runs on that PC:
it reads Tally through TallyPrime's local XML server with **export requests only** and sends the data out to this
platform over HTTPS. The platform never connects to the PC and can ask it only one thing: "read Tally again now".

Decisions: `docs/DECISIONS.md` D108-D113. Code: `bridge/tally/` (the bridge), `backend/app/connectors/tally.py`,
`backend/app/tally/`, `backend/app/api/bridge.py` and `backend/app/api/tally.py` (the platform).

- [Part A: try it on this computer (no Windows, no Tally)](#part-a-try-it-on-this-computer)
- [Part B: a customer's Tally PC](#part-b-a-customers-tally-pc)
- [Part C: building the Windows bridge](#part-c-building-the-windows-bridge)
- [What is least certain (real Tally is untested)](#what-is-least-certain)
- [Settings](#settings)

---

## Part A: try it on this computer

Real Tally cannot run here, so development uses two stand-ins:

- the **Tally stand-in** (`tally_bridge.dev.stand_in`): answers Tally's XML export requests for the demo company
  "UrbanCart Retail (demo)", built from `eval/fixtures/` (run `make fixtures` once). It refuses, and reports, any
  request that would change data;
- the **fake helper** (`tally_bridge.dev.fake_helper`): the **real** bridge code, reading the stand-in in-process,
  with switches to simulate a flaky network, a PC going to sleep and Tally being closed.

1. `make dev` and sign in as an Owner.
2. Connections > Add data > **Connect Tally** > **Get a pairing code**. Note the code (e.g. `K7QM-3XRP`).
3. In a terminal: `make tally-fake-helper CODE=K7QM-3XRP`
   - it pairs, reads the demo company (about 30,000 vouchers, under a minute) and keeps running;
   - the connection shows "Reading Tally for the first time", then **Connected**, with seven tables.
4. Try the switches (stop with Ctrl+C, then start again without `CODE`, the pairing is remembered in
   `~/.tally-bridge-dev`):
   - `make tally-fake-helper LIVE=30`: a new sale every 30 s; press **Refresh now** and it arrives within a minute;
   - `make tally-fake-helper FLAKY=0.3`: 30% of requests fail; the bridge backs off and continues where it stopped;
   - `make tally-fake-helper OFFLINE_AFTER=60`: after a minute the "PC" goes silent; after 5 more minutes the
     connection says **Tally PC offline since …** and questions still use the last data;
   - `make tally-fake-helper TALLY_CLOSED_AFTER=60`: the connection says **Tally is not open on …**;
   - `make tally-fake-helper SAMPLE=1`: a small company instead of the demo one.
5. **Unpair this PC** on the connection page: the fake helper stops with "pair it again".

The stand-in also runs on its own as an HTTP server on port 9000, like Tally:
`make tally-stand-in` (and `LIVE=60` for a new sale every minute). Then the bridge's own commands work against it:
`make tally-bridge ARGS="pair K7QM-3XRP --server http://localhost:8000"` and `make tally-bridge ARGS="run"`
(stored in `~/.tally-bridge`).

---

## Part B: a customer's Tally PC

**On the platform (the Owner):** Connections > Add data > **Connect Tally** > **Get a pairing code**. The code works
**once** and for **10 minutes**; **Get a new pairing code** on the connection page makes another (the old one stops
working).

**On the Tally PC:**

1. **Turn on Tally's XML server.** TallyPrime: *Help (F1) > Settings > Connectivity > Client/Server configuration*:
   *TallyPrime acts as* **Both** (or Server), *Port* **9000**, *Enable ODBC* not needed. Restart Tally if asked.
   Tally.ERP 9: *F12 > Advanced Configuration*, same settings. Keep the company open in Tally.
2. **Install the bridge**: copy `tally-bridge.exe` (Part C) to e.g. `C:\Program Files\TallyBridge\`.
3. **Pair** (Command Prompt, as the Windows user who uses Tally):
   `tally-bridge pair K7QM-3XRP --server https://app.example.in`
   - other Tally address: `--tally http://192.168.1.20:9000` (this PC or the office network only);
   - several companies open: `--company "Exact company name"` (`tally-bridge companies` lists them).
     One connection reads **one company**; add another Tally connection for another company.
4. **Start it at log-on:** Task Scheduler > Create Task > *Run only when user is logged on*; Trigger: *At log on*;
   Action: `C:\Program Files\TallyBridge\tally-bridge.exe run`; Settings: *If the task fails, restart every 1 minute*.
   (Tally itself needs a logged-on user, so a Windows service would not help.)
5. Check: `tally-bridge status`. The log is `%LOCALAPPDATA%\TallyBridge\bridge.log`.

**What the bridge does:** every 15 minutes (and within a minute of **Refresh now**) it reads masters (groups,
ledgers, stock items, voucher types) and the vouchers changed since the last read (Tally's AlterID); at least once a
day it reads all vouchers of the last 36 months, month by month, so vouchers deleted in Tally disappear here too. It
survives sleep, lost network and restarts: progress is saved after every batch and continues where it stopped, and
re-sent data never creates duplicates.

**What it stores on the PC:** `%LOCALAPPDATA%\TallyBridge\`: `config.json` (platform address, Tally address, company),
`token.bin` (the device token, protected with Windows DPAPI: only this Windows user can read it), `state.json`
(progress), `outbox.json` (the batch being sent) and `bridge.log` (no Tally data, no token).

**Moving to another PC:** pair the new PC with a new code; the old one is unpaired at once. **Stopping:**
**Unpair this PC** on the connection page (the data already sent stays), then `tally-bridge forget` on the PC.
Removing the connection deletes everything the bridge sent.

**Network:** the PC needs outbound HTTPS (port 443) to the platform only. No inbound port, no VPN.

---

## Part C: building the Windows bridge

On a Windows PC with Python 3.12 and uv:

```
cd bridge\tally
uv sync --group build
uv run pyinstaller tally_bridge.spec
```

Output: `dist\tally-bridge.exe` (one file). The development stand-in and fake helper are left out. Before giving it
to customers: sign it with the company's code-signing certificate (unsigned executables trigger SmartScreen
warnings), put it somewhere Owners can download it and set `TALLY_BRIDGE_DOWNLOAD_URL` so the pairing screen links
to it. Build a new version when the bridge's code changes; the platform does all reading of Tally's text, so most
fixes need no new bridge.

---

## What is least certain

**Real TallyPrime has never been used** (no Windows PC, no Tally licence here). The bridge's requests and the
platform's reading were written from Tally's documented XML interface and widely used integration patterns, and are
tested only against the stand-in, which was written the same way. Before launch, pair one real TallyPrime company and
compare a month's day book, a ledger's balance and the trial balance with Tally's own screens. Least certain, most
likely first:

1. **Voucher lines through the nested `FETCH`** (`AllLedgerEntries.*`, `AllInventoryEntries.AccountingAllocations.*`):
   some Tally versions may need whole sub-objects fetched. Symptom: vouchers arrive with no lines, so `amount` is 0.
2. **Signs:** Tally writes debits as negative amounts in XML; the platform turns them into positive debits. If a real
   company shows sales as debits, the sign rule (`debit_positive`) is wrong for that field. The trial balance test
   (debits = credits) will still pass, so compare with Tally's screens.
3. **Ledger and stock balances** use `$OpeningBalance`/`$ClosingBalance` with the financial year as the period
   (`SVFROMDATE`/`SVTODATE`); whether a collection export honours that period for balances is unconfirmed.
   `$_PrimaryGroup` as a `COMPUTE` likewise (the platform falls back to following parent groups).
4. **The `$AlterID > n` filter** on a voucher collection: if Tally ignores it, every incremental read is a full one
   (still correct, just slower).
5. **Text formats:** quantities (`" 2 Nos"`, compound units), rates (`"499.00/Nos"`), forex amounts
   (`"$ 10 @ ₹ 83/$ = ₹ 830"`), dates in other formats, UTF-16 answers, forbidden characters. Handled as far as known.
6. **Custom voucher types** without their base type in `$Parent` would be classed "other".
7. **Large companies:** month-by-month exports keep Tally responsive in the stand-in; a real company with very busy
   months may still make Tally slow while it exports (the bridge waits up to 3 minutes per request).
8. **Companies kept in another currency** than rupees: amounts are still marked INR. Not detected yet.
9. **Windows-only parts:** the PyInstaller build, DPAPI protection of the token, the single-instance lock
   (`msvcrt`) and Task Scheduler start-up are untested.

---

## Settings

Platform (`.env`, all optional):

| Variable | Default | Meaning |
|---|---|---|
| `TALLY_PAIRING_CODE_MINUTES` | 10 | How long a pairing code works (once) |
| `TALLY_SYNC_INTERVAL_MINUTES` | 15 | How often the bridge reads Tally |
| `TALLY_FULL_SYNC_HOURS` | 24 | A full read (removes deleted vouchers) at least this often |
| `TALLY_HISTORY_MONTHS` | 36 | Months of vouchers read |
| `TALLY_MAX_BATCH_ROWS` | 2000 | Rows per batch |
| `TALLY_MAX_BATCH_BYTES` | 2000000 | Bytes per batch, also after un-gzipping |
| `TALLY_OFFLINE_AFTER_MINUTES` | 5 | No heartbeat this long: "Tally PC offline since …" |
| `TALLY_POLL_WAIT_SECONDS` | 25 | Longest long-poll wait for "read again now" |
| `TALLY_BRIDGE_DOWNLOAD_URL` | (empty) | Where Owners download the Windows bridge |

`API_BASE_URL` is the address shown with the pairing code; outside development it must be `https://`.

The bridge reads these from the platform at pairing and on every heartbeat (clamped to safe ranges). Its own options
are command-line flags (`tally-bridge --help`); `TALLY_BRIDGE_HOME` moves its folder.
