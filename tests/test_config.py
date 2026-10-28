"""Database URLs as hosts hand them out are turned into what Lekka needs."""
from app.config import database_settings


def test_aiven_style_url():
    url, opts = database_settings("mysql://avnadmin:pw@lekka-x.aivencloud.com:12345/defaultdb?ssl-mode=REQUIRED")
    assert url == "mysql+pymysql://avnadmin:pw@lekka-x.aivencloud.com:12345/defaultdb?charset=utf8mb4"
    assert opts["connect_args"] == {"ssl": {"verify_mode": False}} and opts["pool_pre_ping"]


def test_ca_certificate_gives_a_verified_connection():
    pem = "-----BEGIN CERTIFICATE-----\\nABC\\n-----END CERTIFICATE-----"
    url, opts = database_settings("mysql://u:p@h:1/db?ssl-mode=REQUIRED", ca_pem=pem)
    path = opts["connect_args"]["ssl"]["ca"]
    assert open(path).read().splitlines()[0] == "-----BEGIN CERTIFICATE-----"


def test_local_url_unchanged_except_charset():
    url, opts = database_settings("mysql+pymysql://lekka:pw@localhost/lekka_db?charset=utf8mb4")
    assert url == "mysql+pymysql://lekka:pw@localhost/lekka_db?charset=utf8mb4" and "connect_args" not in opts
    assert database_settings("sqlite://")[0] == "sqlite://"


def test_render_postgres_url():
    url, opts = database_settings("postgres://lekka:pw@dpg-x-a.singapore-postgres.render.com/lekka")
    assert url == "postgresql+psycopg://lekka:pw@dpg-x-a.singapore-postgres.render.com/lekka"
    assert "connect_args" not in opts
    assert database_settings("postgresql://u:p@h/db")[0] == "postgresql+psycopg://u:p@h/db"
