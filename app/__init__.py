"""Lekka – cashflow planner. Flask application factory."""
import click
from dotenv import load_dotenv
from flask import Flask

from .extensions import csrf, db, login_manager, migrate


def create_app(config_object=None):
    load_dotenv()
    from .config import Config
    app = Flask(__name__)
    app.config.from_object(config_object or Config)
    if app.config.get("BEHIND_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from . import models  # noqa: F401  (register tables)
    from .models import User

    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(User, int(uid))

    from .blueprints import register
    register(app)

    from .services.common import inr, today
    from .services.entries import slip_note, status, status_label
    app.jinja_env.filters["inr"] = inr
    from .services.history import label as action_label, when
    app.jinja_env.globals.update(status=status, status_label=status_label, slip_note=slip_note, today=today,
                                 when=when, action_label=action_label,
                                 show_cc=app.config.get("SHOW_COST_CENTRES", False))

    _register_cli(app)
    return app


def _register_cli(app):
    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--name", default=None)
    @click.password_option()
    def create_user(username, name, password):
        """Create the Lekka login (version 1 has a single user)."""
        from .models import User
        if User.query.filter_by(username=username).first():
            raise click.ClickException("That username already exists.")
        u = User(username=username, name=name or username)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"User '{username}' created.")

    @app.cli.command("ensure-user")
    def ensure_user():
        """Hosting without a shell (Render free plan): create or update the login from LEKKA_USER and
        LEKKA_PASSWORD (and optional LEKKA_NAME). Does nothing when they are not set."""
        import os
        from .models import User
        username, password = os.environ.get("LEKKA_USER"), os.environ.get("LEKKA_PASSWORD")
        if not username or not password:
            click.echo("LEKKA_USER / LEKKA_PASSWORD not set; no login changed.")
            return
        if len(password) < 8:
            raise click.ClickException("LEKKA_PASSWORD must be at least 8 characters.")
        u = User.query.filter_by(username=username).first()
        if u is None:
            u = User(username=username, name=os.environ.get("LEKKA_NAME") or username)
            u.set_password(password)
            db.session.add(u)
            click.echo(f"User '{username}' created.")
        elif not u.check_password(password):
            u.set_password(password)
            click.echo(f"Password for '{username}' updated.")
        db.session.commit()

    @app.cli.command("set-password")
    @click.argument("username")
    @click.password_option()
    def set_password(username, password):
        from .models import User
        u = User.query.filter_by(username=username).first()
        if not u:
            raise click.ClickException("No such user.")
        u.set_password(password)
        db.session.commit()
        click.echo("Password changed.")

    @app.cli.command("extend-recurring")
    def extend_recurring():
        """Fill every active recurring template up to its horizon (run daily)."""
        from .services.recurrence import extend_all
        n = len(extend_all())
        db.session.commit()
        click.echo(f"{n} new recurring item(s) created.")

    @app.cli.command("seed-demo")
    @click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
    def seed_demo(yes):
        """Load the worked example from the design doc, shifted so its '25 Nov' is today."""
        from .demo import load_demo
        if not yes:
            click.confirm("This adds demo heads and items to the current database. Continue?", abort=True)
        load_demo()
        db.session.commit()
        click.echo("Demo data loaded.")
