from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import click
from flask import Flask

from . import db
from .config import Config

IST = ZoneInfo("Asia/Kolkata")


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    db.init(app.config["DATABASE_URL"])
    if app.config["BEHIND_PROXY"]:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)   # real client IP + https scheme

    from . import auth, routes_api, routes_web
    app.register_blueprint(routes_api.bp)
    app.register_blueprint(routes_web.bp)
    app.jinja_env.globals["csrf_token"] = auth.csrf_token

    @app.template_filter("local")
    def local(dt, fmt="%d %b, %H:%M"):
        return dt.astimezone(IST).strftime(fmt) if dt else "-"

    @app.template_filter("ago")
    def ago(dt):
        if not dt:
            return "never"
        mins = int((datetime.now(timezone.utc) - dt) / timedelta(minutes=1))
        if mins < 1:
            return "just now"
        if mins < 60:
            return f"{mins} min ago"
        if mins < 48 * 60:
            return f"{mins // 60} h ago"
        return f"{mins // 1440} d ago"

    @app.cli.command("init-db")
    def init_db():
        """Create tables (safe to re-run)."""
        db.run_script("schema.sql")
        click.echo("Database ready.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Replace demo data with a fresh simulated 24 hours."""
        from . import service
        with db.connection() as conn:
            service.seed_demo(conn, app.config["ALERT_COOLDOWN_MIN"])
        click.echo("Demo data seeded.")

    return app
