# Connecting Zoho Books

The app reads one Zoho Books organisation: invoices, bills, expenses, contacts, items, the chart of accounts and a
monthly profit and loss report. It asks Zoho only for **read** access (`ZohoBooks.<module>.READ`) and sends only
`GET` requests to Zoho Books, so it can never change anything in your books. Contacts are read with their name, type
and balances only: e-mail addresses, phone numbers, addresses, contact persons, GSTIN/PAN and bank details are never
read.

Amounts stay in **rupees**, as Zoho sends them (unlike Razorpay, which sends paise).

## Part A: trying it without a Zoho account (development only)

Leave `ZOHO_CLIENT_ID` and `ZOHO_CLIENT_SECRET` empty and keep `APP_ENV=development`.

1. `make fixtures` (once), then `make dev`.
2. Sign in as an Owner and open **Connections > Add data > Connect Zoho Books**.
3. A local page, **Zoho stand-in**, lists the read access the app asks for. Press **Accept**.
4. You land on the new connection, **Zoho Books (UrbanCart)**. Seven tables are read in a few seconds. The page shows
   the organisation, the data centre (India) and today's Zoho API calls.
5. The demo organisation's **expenses** are the monthly actual spend per department in
   `eval/fixtures/zoho_budget_expenses.xlsx` (expense account = department), to the rupee. Upload that workbook too
   and ask "Which department went over budget in Q3 2026?": Marketing is about 22% over, as in `ANSWER_KEY.md`.
   Zoho's API has no budgets, so the budget itself still comes from the workbook (DECISIONS D98).
6. **Read another organisation** on the connection page switches to a second, tiny demo organisation
   (UrbanCart Labs) and reads it from the start.

## Part B: a real Zoho API client (needed for real organisations)

1. Open the Zoho API console for your data centre: **https://api-console.zoho.in** (India). Other data centres use
   their own domain (`api-console.zoho.com`, `.eu`, `.com.au`, `.jp`, `zohocloud.ca`, `.sa`).
2. **Add client > Server-based Applications**:
   - **Homepage URL:** your web app, e.g. `https://app.example.in`.
   - **Authorized Redirect URIs:** `<API_BASE_URL>/connections/zoho/callback`, e.g.
     `http://localhost:8000/connections/zoho/callback` locally.
3. Under the client's **Settings**, turn on **Multi-DC** if customers may have Zoho accounts in other data centres.
   Zoho then sends each person back with their own data centre, and the app uses it. Only Zoho's own data
   centres are accepted.
4. Copy the **Client ID** and **Client Secret** into `.env`:

   ```
   ZOHO_CLIENT_ID=<client id>
   ZOHO_CLIENT_SECRET=<client secret>
   ```

   Optional settings (see `.env.example`): `ZOHO_REGION` (default `in`), `ZOHO_SCOPES`, `ZOHO_HISTORY_DAYS`,
   `ZOHO_REPORT_MONTHS`, `ZOHO_DAILY_CALL_LIMIT`.
5. Restart the API and the worker (`make dev`). The stand-in is then off and "Connect Zoho Books" goes to Zoho.
6. Connect once with a **test organisation** and compare a few invoices and the month's profit and loss with the
   Zoho Books screens before you rely on the numbers.

### Scopes

The app requests these, all read-only:

| Scope | Used for |
|---|---|
| `ZohoBooks.invoices.READ` | invoices |
| `ZohoBooks.bills.READ` | bills |
| `ZohoBooks.expenses.READ` | expenses |
| `ZohoBooks.contacts.READ` | contacts (name, type, balances only) |
| `ZohoBooks.settings.READ` | the organisation list, items |
| `ZohoBooks.accountants.READ` | the chart of accounts |

`ZOHO_SCOPES` can change the list, but anything that is not a `.READ` scope is dropped before it is sent, and a
connection whose token can write is refused.

### Reports

The profit and loss table is read from Zoho's report endpoint (`/reports/profitandloss`), one month at a time.
Zoho's documented scope list has no separate scope for reports. If your organisation refuses the report, only that
table shows the message "Zoho Books did not allow reading the profit and loss report"; the other tables keep working.
Then check in the API console which scope Zoho names for reports, add it to `ZOHO_SCOPES` and sign in to Zoho again
on the connection page.

### Daily API limits

Zoho counts API calls per organisation per day (Free 1,000; Standard 2,000; Professional 5,000; Premium and above
10,000) and per minute (100). Set `ZOHO_DAILY_CALL_LIMIT` to your plan's daily figure. The app:

- counts every call (shown on the connection page as "Zoho API calls today");
- slows down near the per-minute limit;
- stops reading at **90%** of the daily limit, leaving the rest for your own tools, and keeps answering questions
  from the last data it read until the next day (the answer's sources show when that data was read);
- reads only rows changed since the last read after the first time, and the profit and loss report only for the last
  two months, so a refresh normally costs a handful of calls.

A first read of a large organisation costs about one call per 200 invoices, bills or expenses plus 24 calls for the
profit and loss report.

### Removing the connection

Removing a Zoho Books connection deletes its stored data and tells Zoho to revoke the app's access at once. You can
also check **Zoho Accounts > Security > Connected apps**.

## What is not read

- Budgets: Zoho Books' API has no budgets endpoint. Keep the budget in a spreadsheet (upload or Google Sheets); the
  Zoho expenses give the actual spend.
- Journals, bank transactions, credit notes, payments received and vendor payments (not needed for the analyses yet).
- Anything personal about contacts beyond their name (see the top of this page).
