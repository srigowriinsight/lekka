"""Configuration, read from environment variables (see .env.example)."""
import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-.env")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "mysql+pymysql://lekka:lekka@localhost/lekka_db"
    )
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}
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
    SQLALCHEMY_DATABASE_URI = os.environ.get("LEKKA_TEST_DATABASE_URL", "sqlite://")
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    FIXED_TODAY = "2026-11-25"
    SECRET_KEY = "test"
