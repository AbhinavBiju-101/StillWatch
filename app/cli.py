"""Administrator commands: python -m flask --app app <command>. They need DATABASE_URL, so they are
how the person who runs the server (not an operator in the browser) manages devices, firmware and accounts."""
import re
from pathlib import Path

import click
from werkzeug.security import generate_password_hash

from . import auth, db, devices, schema, service, training

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,39}$")
RESERVED = ("demo", "testing")


def register(app):
    @app.cli.command("register-device")
    @click.argument("device_id")
    @click.option("--name", help="display name for the tank (default: the device id)")
    def register_device(device_id, name):
        """Create a node and print its token ONCE (running it again replaces the token)."""
        if not schema.DEVICE_ID_RE.match(device_id):
            raise click.ClickException("device id must be 1-64 chars of letters, digits, _ or -")
        with db.connection() as conn:
            try:
                token = devices.register(conn, device_id, name)
            except ValueError as e:
                raise click.ClickException(str(e))
        click.echo(f"Device {device_id} is registered. Put this in firmware/water_node/secrets.h "
                   "as DEVICE_TOKEN (it is not stored anywhere readable and cannot be shown again):\n")
        click.echo(f"  {token}\n")

    @app.cli.command("revoke-device")
    @click.argument("device_id")
    def revoke_device(device_id):
        """Stop accepting a node's token (e.g. a lost or stolen device)."""
        with db.connection() as conn:
            n = devices.revoke(conn, device_id)
        click.echo("Revoked." if n else "No such device.")

    @app.cli.command("fw-release")
    @click.argument("image", type=click.Path(exists=True, dir_okay=False))
    @click.argument("version")
    @click.option("--notes", default=None)
    def fw_release(image, version, notes):
        """Store a compiled firmware image (the .bin from Arduino's Export Compiled Binary) as VERSION."""
        data = Path(image).read_bytes()
        with db.connection() as conn:
            try:
                digest = devices.add_release(conn, version, data, notes)
            except ValueError as e:
                raise click.ClickException(str(e))
        click.echo(f"Stored {version}: {len(data)} bytes, sha256 {digest}")
        click.echo("It is not sent anywhere until you assign it:  flask --app app fw-assign "
                   f"{version} --device <id>")

    @app.cli.command("fw-assign")
    @click.argument("version")
    @click.option("--device", "device_id", help="one device id")
    @click.option("--all", "everyone", is_flag=True, help="every registered device")
    def fw_assign(version, device_id, everyone):
        """Offer VERSION to a device (or all). Use 'none' to withdraw an offer."""
        if bool(device_id) == everyone:
            raise click.ClickException("give exactly one of --device ID or --all")
        with db.connection() as conn:
            try:
                n = devices.assign(conn, None if everyone else device_id, None if version == "none" else version)
            except ValueError as e:
                raise click.ClickException(str(e))
        click.echo(f"{n} device(s) updated. A node installs it at its next check-in.")

    @app.cli.command("sweep")
    def sweep():
        """Open 'device offline' alerts for silent nodes. Run it from cron, or call POST /api/sweep."""
        from . import notify
        with db.connection() as conn:
            ids = service.sweep_devices(conn, app.config["DEVICE_SILENT_MIN"])
        for alert_id in ids:
            notify.notify_alert(app.config, alert_id)
        click.echo(f"{len(ids)} device alert(s) opened.")

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--dataset", type=click.Choice(["pilot", "demo"]), default="pilot", show_default=True)
    @click.password_option()
    def create_user(username, dataset, password):
        """Add a named operator account."""
        username = username.lower()
        if not USERNAME_RE.match(username) or username in RESERVED:
            raise click.ClickException("username: 3-40 chars, start with a letter, use a-z 0-9 _ . -; "
                                       "'demo' and 'testing' are reserved")
        if len(password) < 10:
            raise click.ClickException("password must be at least 10 characters")
        with db.connection() as conn:
            n = db.run(conn, "insert into users (username, password_hash, dataset) values (%s, %s, %s) "
                             "on conflict (username) do nothing", (username, generate_password_hash(password), dataset))
        click.echo("User created." if n else "That username already exists.")

    @app.cli.command("set-password")
    @click.argument("username")
    @click.password_option()
    def set_password(username, password):
        if len(password) < 10:
            raise click.ClickException("password must be at least 10 characters")
        with db.connection() as conn:
            n = db.run(conn, "update users set password_hash = %s where username = %s",
                       (generate_password_hash(password), username.lower()))
        click.echo("Password changed." if n else "No such user.")

    @app.cli.command("disable-user")
    @click.argument("username")
    @click.option("--enable", is_flag=True, help="turn the account back on")
    def disable_user(username, enable):
        with db.connection() as conn:
            n = db.run(conn, "update users set disabled = %s where username = %s", (not enable, username.lower()))
        click.echo("Done." if n else "No such user.")

    @app.cli.command("api-token")
    @click.argument("username")
    def api_token(username):
        """Issue (or replace) a user's API token. Shown once."""
        token = auth.new_user_token()
        with db.connection() as conn:
            n = db.run(conn, "update users set api_token_hash = %s where username = %s and not disabled",
                       (auth.hash_token(token), username.lower()))
        if not n:
            raise click.ClickException("no such (enabled) user")
        click.echo(f"API token for {username} (use as 'Authorization: Bearer ...'; shown once):\n\n  {token}\n")

    @app.cli.command("export-training")
    @click.option("--dataset", type=click.Choice(["pilot", "demo"]), default="pilot", show_default=True)
    @click.option("--out", "out_dir", default="training-export", show_default=True, type=click.Path(file_okay=False))
    @click.option("--window-hours", default=training.WINDOW_H, show_default=True, type=click.IntRange(1, 48))
    def export_training(dataset, out_dir, window_hours):
        """Write windows.csv (one row per lab sample: sensor features before it + lab results) and
        alerts.csv (every water alert with the operator's verdict)."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        with db.connection() as conn:
            rows = training.load_rows(conn, dataset, window_hours)
            alerts = training.load_alerts(conn, dataset)
        (out / "windows.csv").write_text(training.to_csv(rows), encoding="utf-8")
        (out / "alerts.csv").write_text(training.to_csv(alerts) if alerts else "", encoding="utf-8")
        click.echo(f"{len(rows)} lab sample(s) and {len(alerts)} alert(s) written to {out}/")
