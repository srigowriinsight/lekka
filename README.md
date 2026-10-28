# Lekka – cashflow planner

Lekka is the finance team's cashflow planner. It follows the **Lekka – Cashflow Application System Design**
doc (Design, Screens, Test cases, Edge cases and Prerequisites tabs).

Stack: Python 3.11+, Flask, MariaDB 10.11 (any 10.2+), SQLAlchemy, Alembic migrations, plain HTML/CSS/JS.

## What works in this version

| Area | What you can do |
| --- | --- |
| Grid page | Day, Week, Month, Quarter, Year and Custom views (Indian financial year); Day view shows every day, by default the last 10 days, today and the next 5; heads as rows; expand a head or Expand all to see titles; cell drawer with Tick / Reschedule / Split / Cancel; double-click a figure to change its amount; status bar, item count and overdue corner; Pending / Settled filter; optional Hide empty days; compact amounts; cost-centre filter; Excel export |
| Balance rule | Up to and including today: only ticked items count, on their settled date. From tomorrow: unticked items count on their planned date. Unticked items dated today or earlier count nowhere until ticked or rescheduled (shown italic). Opening = previous closing |
| Expense and Revenue pages | Grouped by head; summary boxes; status filters; search in titles, remarks and PO numbers; tick or reschedule many at once |
| Items | Add from either page or the grid (double-click a cell, or + on a head); title, head, amount, planned date, remarks (optional), counterparty, cost centre, PO no.; "+ New head" inline |
| Linking | "Already received / paid" + a past or today date: Lekka offers the matching open plan, then ticks it (no duplicate). Less than planned splits the plan; more lets you record the extra or raise the plan |
| Splits | By amount or %; any depth; edit an existing split; ticked parts locked; parent total = sum of parts; remove split |
| Tick / untick | Settled date: on the planned date, today, or a picked date (never future); late ticks recalculate every balance from that date |
| Reschedule | One item or a parent (all unticked parts move together); original date and every move kept; end-of-day Review page |
| Cancel | Reason required; for a split part you choose Shrink or Move (nothing pre-selected); last part cancelled cancels the parent |
| Recurring templates | More → End series or Delete (handled dates stay as one-off items); title, head, inflow/outflow, amount, daily/weekly/monthly/quarterly/yearly, every N, day (or last day), start; optional end, count, plan-until, split pattern; edit whole series or from a date; pause / resume (with or without skipped dates); end; delete |
| Adjustments | + or − on any day up to today, remarks optional; own row in the grid |
| Account | Single account with opening balance and date; bank balance checks with the difference |
| Master data | Heads (one head for both directions, order = grid row order). Cost centres and counterparties are hidden for now; set `LEKKA_COST_CENTRES=1` in `.env` to show them |
| History | Every change with user and time; per-item history in Details |

Not in this version (as agreed): import from Excel, more users and roles, low-balance threshold colour,
holiday calendar.

## Set up on Windows (first time)

1. **Database.** Open the MariaDB command line (or HeidiSQL) as root and run:

   ```sql
   CREATE DATABASE lekka_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'lekka'@'localhost' IDENTIFIED VIA mysql_native_password USING PASSWORD('choose-a-strong-password');
   CREATE USER 'lekka'@'127.0.0.1' IDENTIFIED VIA mysql_native_password USING PASSWORD('choose-a-strong-password');
   GRANT ALL PRIVILEGES ON lekka_db.* TO 'lekka'@'localhost';
   GRANT ALL PRIVILEGES ON lekka_db.* TO 'lekka'@'127.0.0.1';
   FLUSH PRIVILEGES;
   ```

2. **Python environment.** In the `lekka` folder:

   ```bat
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Settings.** Copy `.env.example` to `.env` and put in the database password and a long random `SECRET_KEY`.

4. **Create the tables and the login.**

   ```bat
   flask db upgrade
   flask create-user finance --name "Finance"
   ```

5. **(Optional) demo data.** `flask seed-demo` loads the worked example from the design doc, shifted so its
   "25 Nov" is today. Use a separate database for this if you want to keep the real one clean.

6. **Set the real opening balance.** Log in, open **Account**, and enter the opening balance and its date.
   Then add your heads in **Master data** and your regular items in **Templates**.

## Every day

- **Start:** double-click `start_lekka.bat` (opens http://localhost:8000).
  To share it with your manager: `start_lekka.bat manager@company.com`. The Cloudflare Quick Tunnel prints a
  `https://….trycloudflare.com` link; only that email can open it (one-time PIN). The link changes on every start.
  Install cloudflared once with `winget install --id Cloudflare.cloudflared`.
- **Back up:** run `backup_db.bat` at the end of the day (writes `backups\lekka_YYYYMMDD.sql`).
- **Recurring items** are topped up to their horizon automatically whenever the grid opens; `flask extend-recurring`
  does the same from the command line.

## Tests

```bat
pytest
```

104 tests cover the worked example, the balance rule, splits, cancelling, linking, recurrence, adjustments and
every page. Comments in the tests name the matching test-case IDs from the design doc (TC-…, EC-…, PS-…).
They run on SQLite by default; to run them on MariaDB, create an empty `lekka_test` database and set
`LEKKA_TEST_DATABASE_URL` (see `.env.example`).

## Useful commands

| Command | What it does |
| --- | --- |
| `flask db upgrade` | Create or update the tables |
| `flask create-user NAME` | Create the login |
| `flask set-password NAME` | Change a password |
| `flask seed-demo` | Load the design doc's worked example |
| `flask extend-recurring` | Fill recurring templates up to their horizon |
| `flask db migrate -m "..."` | After changing `app/models.py`: create a new migration |

## Project layout

```text
app/
  __init__.py        app factory and CLI commands
  config.py          settings from .env
  models.py          tables (Design tab, section 8)
  services/          all business rules
    balances.py      the balance engine behind the grid
    entries.py       create, split, tick, reschedule, cancel, link, adjustments
    recurrence.py    recurring templates
    common.py        today in IST, money parsing, audit log, ₹ formatting
  blueprints/        pages and popups (thin; they call services)
  templates/         HTML (partials/ = popups)
  static/            CSS and JS
migrations/          database migrations (Alembic)
tests/               pytest suite
```

All rules live in `app/services`; pages only call them. Money is stored as DECIMAL and handled as Python
`Decimal`; all dates are in IST; every change is written to the audit log.
