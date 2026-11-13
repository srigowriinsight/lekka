# Deploying Lekka on the company server

This guide is for the IT team. Lekka is a Flask app that needs Python, a database and a reverse proxy for HTTPS. It
runs on Linux or Windows and works with MariaDB/MySQL or Postgres; the steps below use **Linux + MariaDB + nginx**,
with Windows notes at the end.

| Item | Value |
| --- | --- |
| Python | 3.11 or newer (developed on 3.13) |
| Database | MariaDB 10.6+ (recommended) or Postgres 14+ |
| App server | `waitress` (in requirements.txt), listening on `127.0.0.1:8000` |
| Users | Finance team only. Keep it on the company network or VPN, not the open internet |
| Time zone | Lekka always works in IST (`TZ=Asia/Kolkata`), whatever the server clock is |

---

## Before deployment

1. **Repository access.** The GitHub repository should be **private**. The repository owner adds the IT team under
   *Settings → Collaborators*.
2. **Decide on data.** Either start clean (usual for go-live) or carry over the test data from Render. If carrying
   it over, take the copy *before* the Render free database expires (30 days after it was created).
3. **Server ready:** Python 3.11+, `python3-venv`, `git`, MariaDB server and client (`mariadb-dump`), nginx, and an
   HTTPS certificate for an internal name such as `lekka.company.local`.
4. **Create the database and its user** (as MariaDB root):

   ```sql
   CREATE DATABASE lekka_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'lekka'@'localhost' IDENTIFIED BY 'a-strong-password';
   GRANT ALL PRIVILEGES ON lekka_db.* TO 'lekka'@'localhost';
   FLUSH PRIVILEGES;
   ```

5. **Get the code and install it:**

   ```bash
   sudo useradd --system --create-home --home-dir /opt/lekka lekka     # a service account
   sudo -u lekka git clone https://github.com/srigowriinsight/lekka.git /opt/lekka/app-src
   cd /opt/lekka/app-src
   sudo -u lekka python3 -m venv venv
   sudo -u lekka venv/bin/pip install -r requirements.txt
   ```

6. **Settings.** Copy `.env.example` to `.env` (readable only by the service account: `chmod 600 .env`):

   ```ini
   DATABASE_URL=mysql+pymysql://lekka:a-strong-password@localhost/lekka_db?charset=utf8mb4
   SECRET_KEY=<output of: python3 -c "import secrets; print(secrets.token_hex(32))">
   TZ=Asia/Kolkata
   FLASK_APP=app:create_app
   LEKKA_BEHIND_PROXY=1        # only when users reach it over HTTPS through nginx (secure cookies)
   ```

   Never commit `.env`. It is already in `.gitignore`.

7. **Create the tables and the first login:**

   ```bash
   sudo -u lekka venv/bin/flask db upgrade
   sudo -u lekka venv/bin/flask create-user finance --name "Finance"     # asks for a password (8+ chars)
   ```

   If carrying over Render data, load it now, before anyone logs in. See *Moving the Render test data* below.

8. **Run it as a service.** Create `/etc/systemd/system/lekka.service`:

   ```ini
   [Unit]
   Description=Lekka cashflow
   After=network.target mariadb.service

   [Service]
   User=lekka
   WorkingDirectory=/opt/lekka/app-src
   ExecStartPre=/opt/lekka/app-src/venv/bin/flask db upgrade
   ExecStartPre=/opt/lekka/app-src/venv/bin/flask extend-recurring
   ExecStart=/opt/lekka/app-src/venv/bin/waitress-serve --listen=127.0.0.1:8000 --threads=8 --call app:create_app
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```

   ```bash
   sudo systemctl daemon-reload && sudo systemctl enable --now lekka
   ```

9. **nginx in front (HTTPS):**

   ```nginx
   server {
       listen 443 ssl;
       server_name lekka.company.local;
       ssl_certificate     /etc/ssl/lekka.crt;
       ssl_certificate_key /etc/ssl/lekka.key;
       client_max_body_size 5m;
       location / {
           proxy_pass http://127.0.0.1:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   server { listen 80; server_name lekka.company.local; return 301 https://$host$request_uri; }
   ```

   Serving plain HTTP only? Then leave `LEKKA_BEHIND_PROXY` out of `.env`, or the login cookie is not sent.

---

## After deployment

1. **Smoke test** at `https://lekka.company.local`:
   - the login page opens and `finance` can log in;
   - the date at the top right is today's date in IST;
   - the Grid, Expense, Revenue, Review and Master data pages open;
   - add a test item, tick it, then cancel it;
   - Review → ⤓ Excel downloads a file.

   `sudo journalctl -u lekka -n 50` shows the app log if anything fails.
2. **Finance team set-up** (in the app):
   - on the **Users** page, change the first login's password and add each person;
   - set the real opening balance and date under **Account**;
   - fill Master data (heads, parties, categories, price list), then the recurring templates.
3. **Backups.** `backup_db.sh` dumps the database to `backups/` (gzip) and keeps 30 days. Schedule it with
   `crontab -u lekka -e`:

   ```cron
   30 23 * * * /opt/lekka/app-src/backup_db.sh >> /opt/lekka/app-src/backups/backup.log 2>&1
   ```

   Also copy `backups/` to another machine or the company backup system. Test one restore early:
   `gunzip < backups/lekka_YYYYMMDD_HHMM.sql.gz | mariadb -u lekka -p lekka_db_restore_test`.
4. **Run the test plan.** Finance runs the *Lekka – Test plan* checklist on the live site.
5. **Retire Render.** Once the company server is in use, suspend the Render web service and delete the Render
   database so no copy of company data stays there.

---

## Updating to a new version

```bash
cd /opt/lekka/app-src
sudo -u lekka ./backup_db.sh                           # always back up first
sudo -u lekka git pull
sudo -u lekka venv/bin/pip install -r requirements.txt
sudo systemctl restart lekka                           # runs `flask db upgrade` before starting
```

Database changes come as migrations in `migrations/versions/` and are applied by `flask db upgrade`. To go back one
version: `flask db downgrade`, then check out the previous commit.

---

## Moving the Render test data (optional)

- **To Postgres:** take `pg_dump "<Render external URL>" -Fc -f render.dump`. Restore it into the new, *empty*
  database with `pg_restore --no-owner -d <db> render.dump`, then run `flask db upgrade`. Skip `create-user` in step
  7, because the logins come with the data.
- **To MariaDB:** a Postgres dump can't be loaded into MariaDB. Run `flask db upgrade` on the new database, then
  copy the rows table by table with a short SQLAlchemy script (ask Lily for it).
- **Either way:** check that the row counts match. Users keep their Render passwords, since only password hashes
  are copied.

---

## Windows server instead of Linux

- Install Python and MariaDB. Use `python -m venv venv`, `venv\Scripts\pip install -r requirements.txt`, and the
  same `.env` and `flask` commands.
- Run the start command as a Windows service (for example with NSSM):
  `venv\Scripts\waitress-serve.exe --listen=127.0.0.1:8000 --call app:create_app` in the `lekka` folder, after
  `flask db upgrade`.
- Put IIS (with URL Rewrite + ARR) or nginx for Windows in front for HTTPS.
- Schedule `backup_db.bat` in Task Scheduler. It asks for the password, so for unattended runs give `mariadb-dump`
  an option file (`--defaults-extra-file`) instead of `-p`.

---

## Useful commands

| Command | What it does |
| --- | --- |
| `flask db upgrade` | Create or update the tables |
| `flask create-user NAME` | Create a login (the Users page does this too) |
| `flask set-password NAME` | Reset a forgotten password from the server |
| `flask extend-recurring` | Fill recurring items up to their horizon (also runs when the grid opens) |
| `pytest` | Run the tests (162; SQLite by default, or set `LEKKA_TEST_DATABASE_URL` to an empty test DB) |
