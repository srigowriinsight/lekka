"""Configuration, read from environment variables (see .env.example)."""
import os
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def database_settings(url: str, ca_pem: str | None = None):
    """Accept the URL as hosts give it (e.g. Aiven: mysql://user:pw@host:port/db?ssl-mode=REQUIRED) and turn it
    into what SQLAlchemy + PyMySQL need. Returns (url, engine options)."""
    if url.startswith("mysql://"):
        url = "mysql+pymysql://" + url[len("mysql://"):]
    # Render's own Postgres hands out postgres:// or postgresql:// URLs; name the driver (psycopg 3).
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix):]
    opts = {"pool_pre_ping": True, "pool_recycle": 280}
    if not url.startswith("mysql+pymysql://"):
        return url, opts
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    mode = (query.pop("ssl-mode", None) or query.pop("ssl_mode", None) or query.pop("sslmode", None) or "").upper()
    query.setdefault("charset", "utf8mb4")
    url = urlunsplit(parts._replace(query=urlencode(query)))
    if ca_pem:
        # Verified connection: the host's CA certificate, pasted into an environment variable.
        path = os.path.join(tempfile.gettempdir(), "lekka-db-ca.pem")
        with open(path, "w") as f:
            f.write(ca_pem.replace("\\n", "\n"))
        opts["connect_args"] = {"ssl": {"ca": path}}
    elif mode in ("REQUIRED", "REQUIRE", "VERIFY_CA", "VERIFY_IDENTITY"):
        opts["connect_args"] = {"ssl": {"verify_mode": False}}   # encrypted
    return url, opts


_DB_URL, _DB_OPTS = database_settings(
    os.environ.get("DATABASE_URL", "mysql+pymysql://lekka:lekka@localhost/lekka_db"),
    os.environ.get("DB_CA_CERT") or None)


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-.env")
    SQLALCHEMY_DATABASE_URI = _DB_URL
    SQLALCHEMY_ENGINE_OPTIONS = _DB_OPTS
    # On Render (or any HTTPS host behind a proxy): secure cookies, and trust the proxy's https header.
    BEHIND_PROXY = bool(os.environ.get("RENDER") or os.environ.get("LEKKA_BEHIND_PROXY"))
    SESSION_COOKIE_SECURE = BEHIND_PROXY
    REMEMBER_COOKIE_SECURE = BEHIND_PROXY
    SESSION_COOKIE_SAMESITE = "Lax"
    # Lekka's "today" is always Indian time, whatever the server's clock says.
    TIMEZONE = os.environ.get("TZ", "Asia/Kolkata")
    # For demos and tests only: pretend today is this date (YYYY-MM-DD).
    FIXED_TODAY = os.environ.get("LEKKA_FIXED_TODAY") or None
    DUE_SOON_DAYS = int(os.environ.get("LEKKA_DUE_SOON_DAYS", "7"))
    DEFAULT_HORIZON_MONTHS = int(os.environ.get("LEKKA_HORIZON_MONTHS", "12"))
    # Cost centres and counterparties are kept in the database but hidden for now; set to 1 to show them again.
    SHOW_COST_CENTRES = os.environ.get("LEKKA_COST_CENTRES", "0") == "1"
    WTF_CSRF_TIME_LIMIT = None


class TestConfig(Config):
    TESTING = True
    # Set LEKKA_TEST_DATABASE_URL to run the tests on MariaDB (an empty test database).
    SQLALCHEMY_DATABASE_URI = database_settings(os.environ.get("LEKKA_TEST_DATABASE_URL", "sqlite://"))[0]
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    FIXED_TODAY = "2026-11-25"
    SECRET_KEY = "test"
