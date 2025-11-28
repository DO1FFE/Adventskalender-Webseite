# Erik Schauer, DO1FFE, do1ffe@darc.de
# Adventskalender Programm mit Webserver, Cookie-Unterstützung, farbigen Türchen und QR-Code Download
# Erstelldatum: 28.11.2023

import logging
import datetime
import random
import qrcode
import os
import json
import pytz
import shutil
import sqlite3
from urllib.parse import urlparse
from flask import (
    Flask,
    request,
    make_response,
    render_template_string,
    send_from_directory,
    session,
    redirect,
    url_for,
)
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError, generate_csrf, validate_csrf
from markupsafe import Markup, escape
from werkzeug.security import generate_password_hash, check_password_hash
from wtforms.validators import ValidationError

# Logging-Konfiguration
logging.basicConfig(filename='debug.log', level=logging.DEBUG, 
                    format='%(asctime)s %(levelname)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

# Debugging-Flag
DEBUG = True

# Lokale Zeitzone festlegen
local_timezone = pytz.timezone("Europe/Berlin")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "please-change-me")
csrf = CSRFProtect()
csrf.init_app(app)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
USER_DATABASE = os.path.join(BASE_DIR, "users.db")
ADMIN_EMAIL = "do1ffe@darc.de"

CSRF_ERROR_MESSAGE = (
    "Ungültiges oder fehlendes Sicherheits-Token. Bitte lade die Seite neu und "
    "versuche es erneut."
)


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": generate_csrf}


def get_db_connection():
    connection = sqlite3.connect(USER_DATABASE)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
    except sqlite3.DatabaseError as exc:
        logging.error("PRAGMA foreign_keys konnte nicht gesetzt werden: %s", exc)
    return connection


def normalise_email(email):
    if email is None:
        return ""
    return email.strip().lower()


def generate_placeholder_email(existing_emails, user_id):
    base_email = f"user-{user_id}@example.invalid"
    candidate = base_email
    suffix = 1

    while candidate in existing_emails:
        candidate = f"user-{user_id}-{suffix}@example.invalid"
        suffix += 1

    existing_emails.add(candidate)
    return candidate


def sanitize_user_records(connection):
    try:
        cursor = connection.execute(
            "SELECT id, email, display_name, password_hash FROM users ORDER BY id"
        )
    except sqlite3.DatabaseError as exc:
        logging.error("Benutzer konnten nicht geprüft werden: %s", exc)
        return

    rows = cursor.fetchall()
    existing_emails = {
        normalise_email(row["email"])
        for row in rows
        if normalise_email(row["email"])
    }

    sanitized = 0

    for row in rows:
        raw_email = row["email"]
        email = normalise_email(raw_email)
        raw_display_name = row["display_name"]
        display_name = (raw_display_name or "").strip()
        password_hash = row["password_hash"]
        needs_update = False

        if not email:
            email = generate_placeholder_email(existing_emails, row["id"])
            needs_update = True
        else:
            existing_emails.add(email)
            if raw_email != email:
                needs_update = True

        if not display_name:
            display_name = email or f"Benutzer {row['id']}"
            needs_update = True
        elif raw_display_name != display_name:
            needs_update = True

        if password_hash is None:
            password_hash = ""
            needs_update = True

        if needs_update:
            sanitized += 1
            connection.execute(
                "UPDATE users SET email = ?, display_name = ?, password_hash = ? WHERE id = ?",
                (email, display_name, password_hash, row["id"]),
            )

    if sanitized and DEBUG:
        logging.debug("%s Benutzereinträge wurden bereinigt.", sanitized)


def users_table_needs_migration(connection):
    try:
        columns = connection.execute("PRAGMA table_info(users)").fetchall()
    except sqlite3.DatabaseError as exc:
        logging.error("Benutzertabelle konnte nicht geprüft werden: %s", exc)
        return False

    if not columns:
        return True

    column_map = {column["name"]: column for column in columns}
    required_not_null = {
        "email": 1,
        "display_name": 1,
        "password_hash": 1,
    }

    for column_name, required in required_not_null.items():
        column = column_map.get(column_name)
        if not column or column["notnull"] != required:
            return True

    return False


def migrate_users_table(connection):
    sanitize_user_records(connection)

    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("ALTER TABLE users RENAME TO users_legacy")
        connection.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT UNIQUE NOT NULL CHECK (trim(email) <> ''),
                display_name TEXT NOT NULL CHECK (trim(display_name) <> ''),
                password_hash TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO users (id, email, display_name, password_hash)
            SELECT id, email, display_name, COALESCE(password_hash, '')
            FROM users_legacy
            """
        )
        connection.execute("DROP TABLE users_legacy")
    except sqlite3.DatabaseError as exc:
        logging.error("Benutzertabelle konnte nicht migriert werden: %s", exc)
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def init_user_db():
    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL CHECK (trim(email) <> ''),
                    display_name TEXT NOT NULL CHECK (trim(display_name) <> ''),
                    password_hash TEXT NOT NULL
                )
                """
            )

            if users_table_needs_migration(connection):
                migrate_users_table(connection)
            else:
                sanitize_user_records(connection)

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_rewards (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    door INTEGER NOT NULL,
                    prize_name TEXT NOT NULL,
                    sponsor TEXT,
                    sponsor_link TEXT,
                    qr_filename TEXT,
                    qr_content TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, door),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
    except sqlite3.DatabaseError as exc:
        logging.error("Fehler bei der Initialisierung der Benutzerdatenbank: %s", exc)


init_user_db()


def validate_form_csrf(form):
    try:
        validate_csrf(form.get("csrf_token"))
    except (ValidationError, CSRFError) as exc:
        logging.warning("CSRF-Validierung fehlgeschlagen: %s", exc)
        return CSRF_ERROR_MESSAGE
    return ""


def get_user_by_email(email):
    email_normalised = normalise_email(email)
    if not email_normalised:
        return None
    with get_db_connection() as connection:
        cursor = connection.execute(
            "SELECT id, email, display_name, password_hash FROM users WHERE email = ?",
            (email_normalised,),
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id):
    if user_id is None:
        return None
    with get_db_connection() as connection:
        cursor = connection.execute(
            "SELECT id, email, display_name, password_hash FROM users WHERE id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def get_all_users():
    with get_db_connection() as connection:
        cursor = connection.execute(
            """
            SELECT id,
                   COALESCE(email, '') AS email,
                   COALESCE(display_name, '') AS display_name
            FROM users
            ORDER BY LOWER(email)
            """
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def update_user(user_id, email, display_name, password=None):
    if not user_id:
        raise ValueError("Ungültige Benutzer-ID.")

    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("Benutzer konnte nicht gefunden werden.")

    email_normalised = (
        normalise_email(email)
        if email is not None
        else normalise_email(user.get("email"))
    )
    if not email_normalised:
        raise ValueError("Bitte gib eine gültige E-Mail-Adresse an.")

    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("Bitte gib einen Anzeigenamen an.")

    updates = ["email = ?", "display_name = ?"]
    parameters = [email_normalised, display_name]

    if password:
        if len(password) < 8:
            raise ValueError("Das Passwort muss mindestens 8 Zeichen lang sein.")
        password_hash = generate_password_hash(password)
        updates.append("password_hash = ?")
        parameters.append(password_hash)

    parameters.append(int(user_id))

    try:
        with get_db_connection() as connection:
            connection.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
                parameters,
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("Diese E-Mail-Adresse ist bereits registriert.") from exc
    except sqlite3.DatabaseError as exc:
        logging.error("Benutzer %s konnte nicht aktualisiert werden: %s", user_id, exc)
        raise ValueError("Benutzerdaten konnten nicht aktualisiert werden.") from exc

    return get_user_by_id(user_id)


def release_rewards_for_user(rewards):
    if not rewards:
        return False

    prizes = load_prizes()
    if not prizes:
        return False

    updated = False

    def build_key(name, sponsor):
        return (
            str(name or "").strip().lower(),
            str(sponsor or "").strip().lower(),
        )

    lookup = {
        build_key(prize.get("name"), prize.get("sponsor")): prize
        for prize in prizes
    }

    for reward in rewards:
        reward_key = build_key(reward.get("prize_name"), reward.get("sponsor"))
        prize_entry = lookup.get(reward_key)

        if prize_entry is None:
            prize_entry = lookup.get((reward_key[0], ""))

        if prize_entry is None:
            logging.warning(
                "Kein passender Preis zum Freigeben gefunden: %s",
                reward.get("prize_name"),
            )
            continue

        remaining = prize_entry.get("remaining", 0)
        total = prize_entry.get("total", 0)
        if remaining < total:
            prize_entry["remaining"] = min(remaining + 1, total)
            updated = True

    if updated:
        save_prizes(prizes)

    return updated


def cleanup_user_qr_codes(rewards):
    if not rewards:
        return False

    qr_directory = "qr_codes"
    removed_any = False

    for reward in rewards:
        qr_filename = (reward.get("qr_filename") or "").strip()
        if not qr_filename:
            continue
        qr_path = os.path.join(qr_directory, qr_filename)
        if os.path.exists(qr_path) and os.path.isfile(qr_path):
            try:
                os.remove(qr_path)
                removed_any = True
            except OSError as exc:
                logging.error("QR-Code %s konnte nicht gelöscht werden: %s", qr_path, exc)

    return removed_any


def remove_user_from_winners_file(user_id):
    winners_file = "gewinner.txt"
    if not os.path.exists(winners_file):
        return False

    try:
        with open(winners_file, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except OSError as exc:
        logging.error("Gewinnerdatei konnte nicht gelesen werden: %s", exc)
        return False

    prefix = f"{user_id}:"
    filtered_lines = [line for line in lines if not line.strip().startswith(prefix)]

    if len(filtered_lines) == len(lines):
        return False

    try:
        with open(winners_file, "w", encoding="utf-8") as file:
            file.writelines(filtered_lines)
    except OSError as exc:
        logging.error("Gewinnerdatei konnte nicht aktualisiert werden: %s", exc)
        return False

    return True


def delete_user_and_release_rewards(user_id):
    if not user_id:
        raise ValueError("Ungültige Benutzer-ID.")

    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Ungültige Benutzer-ID.") from exc

    with get_db_connection() as connection:
        cursor = connection.execute(
            "SELECT id, email, display_name FROM users WHERE id = ?",
            (user_id_int,),
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError("Benutzer konnte nicht gefunden werden.")

        rewards_cursor = connection.execute(
            """
            SELECT door, prize_name, sponsor, sponsor_link, qr_filename
            FROM user_rewards
            WHERE user_id = ?
            """,
            (user_id_int,),
        )
        rewards = [dict(item) for item in rewards_cursor.fetchall()]

        connection.execute("DELETE FROM users WHERE id = ?", (user_id_int,))

    release_rewards_for_user(rewards)
    cleanup_user_qr_codes(rewards)
    remove_user_from_winners_file(user_id_int)

    return dict(row), rewards


def create_user(email, display_name, password):
    email_normalised = normalise_email(email)
    if not email_normalised or not password:
        raise ValueError("E-Mail-Adresse und Passwort dürfen nicht leer sein.")
    display_name = (display_name or email_normalised).strip()
    if not display_name:
        raise ValueError("Bitte gib einen Anzeigenamen an.")
    password_hash = generate_password_hash(password)
    try:
        with get_db_connection() as connection:
            cursor = connection.execute(
                "INSERT INTO users (email, display_name, password_hash) VALUES (?, ?, ?)",
                (email_normalised, display_name, password_hash),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError as exc:
        raise ValueError("Diese E-Mail-Adresse ist bereits registriert.") from exc
    return get_user_by_id(user_id)


def verify_password(user, password):
    if not user or not password:
        return False
    password_hash = user.get("password_hash")
    if not password_hash:
        return False
    return check_password_hash(password_hash, password)


def is_admin_user(user):
    if not user:
        return False
    return normalise_email(user.get("email")) == normalise_email(ADMIN_EMAIL)

# Initialisierung
tuerchen_status = {tag: set() for tag in range(1, 25)}
PRIZE_FILE = "preise.json"
CALENDAR_STATUS_FILE = "kalender_status.json"
gewinn_zeiten = [12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
tuerchen_farben = ["#FFCCCC", "#CCFFCC", "#CCCCFF", "#FFFFCC", "#CCFFFF", "#FFCCFF", "#FFCC99", "#99CCFF", "#FF9999", "#99FF99", "#9999FF", "#FF9966"] * 2

def load_calendar_status():
    if os.path.exists(CALENDAR_STATUS_FILE):
        try:
            with open(CALENDAR_STATUS_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
            return bool(data.get("active", True))
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            logging.error("Fehler beim Laden des Kalenderstatus: %s", exc)
    return True


def save_calendar_status(active):
    try:
        with open(CALENDAR_STATUS_FILE, "w", encoding="utf-8") as file:
            json.dump({"active": bool(active)}, file)
    except OSError as exc:
        logging.error("Kalenderstatus konnte nicht gespeichert werden: %s", exc)


calendar_active = load_calendar_status()


def set_calendar_active(active):
    global calendar_active
    calendar_active = bool(active)
    save_calendar_status(calendar_active)


def get_calendar_active():
    return bool(calendar_active)


def get_local_datetime():
    utc_dt = datetime.datetime.now(pytz.utc)  # aktuelle Zeit in UTC
    return utc_dt.astimezone(local_timezone)  # konvertiere in lokale Zeitzone


def record_user_reward(user_id, door, prize_name, sponsor=None, sponsor_link=None, qr_filename=None, qr_content=None):
    if not user_id or not prize_name:
        return
    created_at = get_local_datetime().isoformat()
    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO user_rewards (
                    user_id, door, prize_name, sponsor, sponsor_link, qr_filename, qr_content, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, door) DO UPDATE SET
                    prize_name=excluded.prize_name,
                    sponsor=excluded.sponsor,
                    sponsor_link=excluded.sponsor_link,
                    qr_filename=excluded.qr_filename,
                    qr_content=excluded.qr_content,
                    created_at=excluded.created_at
                """,
                (
                    int(user_id),
                    int(door),
                    str(prize_name).strip(),
                    str(sponsor or "").strip() or None,
                    str(sponsor_link or "").strip() or None,
                    str(qr_filename or "").strip() or None,
                    str(qr_content or "").strip() or None,
                    created_at,
                ),
            )
    except sqlite3.DatabaseError as exc:
        logging.error("Nutzergewinn konnte nicht gespeichert werden: %s", exc)


def get_user_rewards(user_id):
    if not user_id:
        return []
    try:
        with get_db_connection() as connection:
            cursor = connection.execute(
                """
                SELECT door, prize_name, sponsor, sponsor_link, qr_filename, qr_content, created_at
                FROM user_rewards
                WHERE user_id = ?
                ORDER BY datetime(created_at) DESC, door DESC
                """,
                (int(user_id),),
            )
            rows = cursor.fetchall()
    except sqlite3.DatabaseError as exc:
        logging.error("Gewinne für Benutzer %s konnten nicht geladen werden: %s", user_id, exc)
        return []

    rewards = []
    for row in rows:
        row_data = dict(row)
        created_at_raw = row_data.get("created_at")
        display_date = created_at_raw
        if created_at_raw:
            try:
                parsed = datetime.datetime.fromisoformat(created_at_raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=pytz.utc).astimezone(local_timezone)
                else:
                    parsed = parsed.astimezone(local_timezone)
                display_date = parsed.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                display_date = created_at_raw
        rewards.append({
            "door": row_data.get("door"),
            "prize_name": row_data.get("prize_name"),
            "sponsor": row_data.get("sponsor"),
            "sponsor_link": row_data.get("sponsor_link"),
            "qr_filename": row_data.get("qr_filename"),
            "qr_content": row_data.get("qr_content"),
            "created_at": created_at_raw,
            "display_date": display_date,
        })
    return rewards


def save_prizes(prizes):
    with open(PRIZE_FILE, "w", encoding="utf-8") as file:
        json.dump(prizes, file, indent=2, ensure_ascii=False)


def load_prizes():
    if os.path.exists(PRIZE_FILE):
        try:
            with open(PRIZE_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
            prizes = []
            for entry in data:
                name = str(entry.get("name", "")).strip()
                sponsor = str(entry.get("sponsor", "") or "").strip()
                sponsor_link = str(entry.get("sponsor_link", "") or "").strip()
                total = int(entry.get("total", entry.get("quantity", 0)))
                total = max(total, 0)
                remaining = int(entry.get("remaining", total))
                remaining = max(min(remaining, total), 0)
                if name and total > 0:
                    prize_entry = {
                        "name": name,
                        "total": total,
                        "remaining": remaining,
                        "sponsor": sponsor,
                        "sponsor_link": sponsor_link,
                    }
                    prizes.append(prize_entry)
            if prizes:
                return prizes
        except (json.JSONDecodeError, ValueError, OSError, TypeError) as exc:
            logging.error("Fehler beim Laden der Preise: %s", exc)
    default_prizes = [
        {
            "name": "Freigetränk",
            "total": 15,
            "remaining": 15,
            "sponsor": "",
            "sponsor_link": "",
        }
    ]
    save_prizes(default_prizes)
    return default_prizes


def get_prize_stats(prizes=None):
    if prizes is None:
        prizes = load_prizes()
    total = sum(prize.get("total", 0) for prize in prizes)
    remaining = sum(prize.get("remaining", 0) for prize in prizes)
    awarded = total - remaining
    return prizes, total, remaining, awarded


def reduce_prize(prizes, current_day=None):
    available = []
    for idx, prize in enumerate(prizes):
        if prize.get("remaining", 0) <= 0:
            continue
        if idx == 0 and current_day not in (None, 24):
            continue
        available.append(prize)
    if not available:
        return None
    weights = [prize["remaining"] for prize in available]
    selected = random.choices(available, weights=weights, k=1)[0]
    selected["remaining"] -= 1
    save_prizes(prizes)
    return selected


def format_prize_lines(prizes):
    lines = []
    for prize in prizes:
        total = prize.get("total", 0)
        remaining = prize.get("remaining", total)
        name = prize.get("name", "")
        sponsor = str(prize.get("sponsor", "") or "").strip()
        sponsor_link = str(prize.get("sponsor_link", "") or "").strip()
        name_segment = name
        if sponsor:
            sponsor_segment = sponsor
            if sponsor_link:
                sponsor_segment = f"{sponsor} ({sponsor_link})"
            name_segment = f"{name} | {sponsor_segment}"
        if remaining != total:
            lines.append(f"{name_segment}={total}/{remaining}")
        else:
            lines.append(f"{name_segment}={total}")
    return "\n".join(lines)


def extract_sponsor_details(sponsor_part):
    """Split a sponsor entry into name and optional link.

    The sponsor segment supports the syntax "Sponsor" or
    "Sponsor (https://link)", where the URL may itself contain parentheses
    or query parameters. The function searches from the end of the string to
    find a trailing "(http...)" segment and separates it from the sponsor
    name when present.
    """

    text = str(sponsor_part or "").strip()
    if not text:
        return "", ""

    sponsor_name = text
    sponsor_link = ""
    stripped = text.rstrip()
    search_position = len(stripped)

    while search_position > 0:
        start_index = stripped.rfind("(", 0, search_position)
        if start_index == -1:
            break
        remainder = stripped[start_index + 1 :].strip()
        if remainder.endswith(")"):
            url_candidate = remainder[:-1].strip()
            if url_candidate and url_candidate.lower().startswith(("http://", "https://")):
                sponsor_name = stripped[:start_index].rstrip()
                sponsor_link = url_candidate
                break
        search_position = start_index

    return sponsor_name, sponsor_link


def parse_prize_configuration(prize_data):
    prizes = []
    for idx, line in enumerate(prize_data.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if "=" not in stripped:
            raise ValueError(
                "Zeile {}: Bitte das Format 'Name=Anzahl', 'Name | Sponsor=Anzahl' "
                "oder 'Name | Sponsor (https://link)=Anzahl' verwenden.".format(idx)
            )
        name_part, amount_part = map(str.strip, stripped.rsplit("=", 1))
        if not name_part:
            raise ValueError(f"Zeile {idx}: Preisname fehlt.")
        if not amount_part:
            raise ValueError(f"Zeile {idx}: Anzahl fehlt.")
        sponsor = ""
        sponsor_link = ""
        if "|" in name_part:
            name_part, sponsor_part = map(str.strip, name_part.split("|", 1))
            sponsor, sponsor_link = extract_sponsor_details(sponsor_part)
        name = name_part
        if not name:
            raise ValueError(f"Zeile {idx}: Preisname fehlt.")
        if "/" in amount_part:
            total_part, remaining_part = map(str.strip, amount_part.split("/", 1))
        else:
            total_part, remaining_part = amount_part, amount_part
        try:
            total = int(total_part)
            remaining = int(remaining_part)
        except ValueError as exc:
            raise ValueError(f"Zeile {idx}: Ungültige Anzahl.") from exc
        total = max(total, 0)
        remaining = max(min(remaining, total), 0)
        if total == 0:
            continue
        prize_entry = {
            "name": name,
            "total": total,
            "remaining": remaining,
            "sponsor": sponsor,
            "sponsor_link": sponsor_link,
        }
        prizes.append(prize_entry)
    if not prizes:
        raise ValueError("Es muss mindestens ein Preis mit positiver Anzahl angegeben werden.")
    return prizes

def hat_gewonnen(user_identifier):
    """Überprüft, ob der Benutzer bereits gewonnen hat."""
    user_identifier = str(user_identifier)
    if not os.path.exists("gewinner.txt"):
        return False
    with open("gewinner.txt", "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            prefix = line.split(" ", 1)[0]
            prefix_id = prefix.split(":", 1)[0]
            if prefix_id == user_identifier:
                return True
    return False


def gewinnchance_ermitteln(user_identifier, heutiges_datum, verbleibende_preise):
    """Berechnet die Gewinnchance anhand der vorhandenen Preise und vergangener Gewinne."""
    verbleibende_tage = 25 - heutiges_datum.day
    if verbleibende_tage <= 0 or verbleibende_preise <= 0:
        return 0

    gewinnchance = verbleibende_preise / verbleibende_tage

    # Reduzierte Gewinnchance für Benutzer, die bereits gewonnen haben
    if hat_gewonnen(user_identifier):
        return gewinnchance * 0.1  # Beispiel: 10% der normalen Gewinnchance

    return gewinnchance


def hat_teilgenommen(user_identifier, tag):
    user_identifier = str(user_identifier)
    if not os.path.exists("teilnehmer.txt"):
        return False
    with open("teilnehmer.txt", "r", encoding="utf-8") as file:
        for line in file:
            cleaned = line.strip()
            if not cleaned:
                continue
            try:
                user_part, tag_part = cleaned.rsplit("-", 1)
            except ValueError:
                continue
            user_key = user_part.split(":", 1)[0]
            if user_key == user_identifier and tag_part == str(tag):
                return True
    return False


def speichere_teilnehmer(user_identifier, display_name, tag):
    user_identifier = str(user_identifier)
    if DEBUG:
        logging.debug(
            "Speichere Teilnehmer %s (%s) für Tag %s",
            user_identifier,
            display_name,
            tag,
        )
    with open("teilnehmer.txt", "a", encoding="utf-8") as file:
        file.write(f"{user_identifier}:{display_name}-{tag}\n")


def speichere_gewinner(user_identifier, display_name, tag, preis, jahr=None, sponsor=None):
    if DEBUG:
        logging.debug(
            "Speichere Gewinner %s (%s) für Tag %s (%s)",
            user_identifier,
            display_name,
            tag,
            preis,
        )
    if jahr is None:
        jahr = get_local_datetime().year
    sponsor_text = ""
    if sponsor:
        sponsor_text = f" - Sponsor: {str(sponsor).strip()}"
    with open("gewinner.txt", "a", encoding="utf-8") as file:
        file.write(
            f"{user_identifier}:{display_name} - Tag {tag} - {preis}{sponsor_text} - OV L11 - {jahr}\n"
        )

@app.route('/login', methods=['GET', 'POST'])
@csrf.exempt
def login():
    if session.get('user_id'):
        return redirect(url_for('startseite'))

    error = ""
    email = ""
    message = ""
    if request.method == 'GET' and request.args.get('registered'):
        message = "Dein Konto wurde erfolgreich erstellt. Bitte melde dich jetzt an."

    if request.method == 'POST':
        csrf_error = validate_form_csrf(request.form)
        if csrf_error:
            error = csrf_error
        else:
            email = (request.form.get('email') or "").strip()
            password = request.form.get('password') or ""
            user = get_user_by_email(email)
            if not user or not verify_password(user, password):
                error = "E-Mail-Adresse oder Passwort sind nicht korrekt."
            else:
                session['user_id'] = user['id']
                return redirect(url_for('startseite'))

    return render_template_string(
        LOGIN_PAGE,
        error=error,
        email=email,
        message=message,
    )


@app.route('/register', methods=['GET', 'POST'])
@csrf.exempt
def register():
    if session.get('user_id'):
        return redirect(url_for('startseite'))

    error = ""
    email = ""
    display_name = ""

    if request.method == 'POST':
        csrf_error = validate_form_csrf(request.form)
        if csrf_error:
            error = csrf_error
        else:
            display_name = (request.form.get('display_name') or "").strip()
            email = (request.form.get('email') or "").strip()
            password = request.form.get('password') or ""
            confirm_password = request.form.get('confirm_password') or ""

            if not display_name:
                error = "Bitte gib einen Anzeigenamen an."
            elif not email:
                error = "Bitte gib eine gültige E-Mail-Adresse an."
            elif not password:
                error = "Bitte wähle ein Passwort."
            elif password != confirm_password:
                error = "Die Passwörter stimmen nicht überein."
            elif len(password) < 8:
                error = "Das Passwort muss mindestens 8 Zeichen lang sein."
            else:
                try:
                    create_user(email, display_name, password)
                except ValueError as exc:
                    error = str(exc)
                else:
                    return redirect(url_for('login', registered=1))

    return render_template_string(
        REGISTER_PAGE,
        error=error,
        email=email,
        display_name=display_name,
    )


@app.route('/logout', methods=['GET'])
def logout():
    session.clear()
    return redirect(url_for('startseite'))


@app.route('/', methods=['GET'])
def startseite():
    user_id = session.get('user_id')
    if DEBUG:
        logging.debug("Startseite aufgerufen - Session User ID: %s", user_id)

    user = get_user_by_id(user_id) if user_id else None
    if user_id and not user:
        session.clear()
        user_id = None
        user = None

    is_logged_in = user is not None
    username = (
        (user.get("display_name") or user.get("email"))
        if user
        else "Gast"
    )
    calendar_active = get_calendar_active()
    heute = get_local_datetime().date()
    if DEBUG:
        logging.debug("Startseite - Heute: %s", heute)

    prizes, max_preise, verbleibende_preise, _ = get_prize_stats()

    prize_names = [prize.get("name", "").strip() for prize in prizes if prize.get("total", 0) > 0]

    sponsors = []
    sponsor_groups = {}
    sponsor_order = []

    def format_sponsor_link_label(url, fallback_label=""):
        fallback = str(fallback_label or "").strip()
        if fallback:
            return fallback
        parsed = urlparse(url)
        host = parsed.netloc
        if host:
            if parsed.path and parsed.path not in {"", "/"}:
                return f"{host}{parsed.path}"
            return host
        return url

    for prize in prizes:
        sponsor_name = str(prize.get("sponsor", "") or "").strip()
        if not sponsor_name:
            continue
        sponsor_link_raw = str(prize.get("sponsor_link", "") or "").strip()
        sponsor_link = sponsor_link_raw or None
        product_name = str(prize.get("name", "") or "").strip()
        normalised_name = sponsor_name.casefold()
        group = sponsor_groups.get(normalised_name)
        if not group:
            group = {"name": sponsor_name, "links": [], "_link_keys": set()}
            sponsor_groups[normalised_name] = group
            sponsor_order.append(normalised_name)
        elif not group.get("name"):
            group["name"] = sponsor_name

        if sponsor_link:
            link_label = format_sponsor_link_label(sponsor_link, product_name)
            key = (sponsor_link, link_label)
            if key not in group.setdefault("_link_keys", set()):
                group["_link_keys"].add(key)
                group["links"].append(
                    {
                        "url": sponsor_link,
                        "label": link_label,
                        "product": product_name,
                    }
                )
        elif product_name:
            key = (None, product_name)
            if key not in group.setdefault("_link_keys", set()):
                group["_link_keys"].add(key)
                group["links"].append(
                    {
                        "url": None,
                        "label": product_name,
                        "product": product_name,
                    }
                )

    for group in sponsor_groups.values():
        group.pop("_link_keys", None)

    sponsors = [sponsor_groups[name] for name in sponsor_order]

    def format_prize_phrase(names):
        filtered = [name for name in names if name]
        if not filtered:
            return ""
        if len(filtered) == 1:
            return filtered[0]
        if len(filtered) == 2:
            return f"{filtered[0]} oder {filtered[1]}"
        return ", ".join(filtered[:-1]) + f" oder {filtered[-1]}"

    prize_phrase = format_prize_phrase(prize_names)

    weihnachten = datetime.date(heute.year, 12, 24)
    if heute.month == 12 and heute > weihnachten:
        tage_bis_weihnachten = 0
    else:
        if heute > weihnachten:
            weihnachten = datetime.date(heute.year + 1, 12, 24)
        tage_bis_weihnachten = max((weihnachten - heute).days, 0)

    tuerchen_status.clear()
    tuerchen_status.update({tag: set() for tag in range(1, 25)})
    if is_logged_in:
        for tag in range(1, 25):
            if hat_teilgenommen(user_id, tag):
                tuerchen_status[tag].add(str(user_id))

    # Zufällige Reihenfolge der Türchen bei jedem Aufruf
    tuerchen_reihenfolge = random.sample(range(1, 25), 24)

    context = {
        "username": username,
        "tuerchen": tuerchen_reihenfolge,
        "heute": heute,
        "tuerchen_status": tuerchen_status,
        "tuerchen_farben": tuerchen_farben,
        "verbleibende_preise": verbleibende_preise,
        "max_preise": max_preise,
        "prize_phrase": prize_phrase,
        "prizes": prizes,
        "sponsors": sponsors,
        "tage_bis_weihnachten": tage_bis_weihnachten,
        "calendar_active": calendar_active,
        "user_email": user.get("email") if user else "",
        "logout_url": url_for('logout') if is_logged_in else None,
        "login_url": url_for('login'),
        "register_url": url_for('register'),
        "is_admin": is_admin_user(user) if user else False,
        "admin_url": url_for('admin_page') if is_logged_in and is_admin_user(user) else None,
        "user_rewards": get_user_rewards(user_id) if is_logged_in else [],
        "is_logged_in": is_logged_in,
    }

    return render_template_string(HOME_PAGE, **context)

@app.route('/oeffne_tuerchen/<int:tag>', methods=['GET'])
def oeffne_tuerchen(tag):
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    user = get_user_by_id(user_id)
    if not user:
        session.clear()
        return redirect(url_for('login'))

    if not get_calendar_active():
        return make_response(
            render_template_string(
                GENERIC_PAGE,
                content="Der Adventskalender ist derzeit nicht aktiv. Bitte schau später noch einmal vorbei.",
            )
        )

    heute = get_local_datetime().date()
    display_name = user.get("display_name") or user.get("email")
    safe_display_name = escape(display_name)
    if DEBUG:
        logging.debug(
            "Öffne Türchen %s aufgerufen - Benutzer: %s (%s), Datum: %s",
            tag,
            user_id,
            display_name,
            heute,
        )

    if heute.month == 12 and heute.day == tag:
        if hat_teilgenommen(user_id, tag):
            if DEBUG:
                logging.debug("Benutzer %s hat Türchen %s bereits geöffnet", user_id, tag)
            return make_response(render_template_string(GENERIC_PAGE, content="Du hast dieses Türchen heute bereits geöffnet!"))

        speichere_teilnehmer(user_id, display_name, tag)
        tuerchen_status[tag].add(str(user_id))

        prizes, max_preise, verbleibende_preise, _ = get_prize_stats()
        if verbleibende_preise <= 0:
            if DEBUG: logging.debug("Keine Preise mehr verfügbar")
            return make_response(render_template_string(GENERIC_PAGE, content="Alle Preise wurden bereits vergeben."))

        gewinnchance = gewinnchance_ermitteln(user_id, heute, verbleibende_preise)
        if DEBUG:
            logging.debug(
                "Gewinnchance für Benutzer %s am Tag %s: %s",
                user_id,
                tag,
                gewinnchance,
            )

        if get_local_datetime().hour in gewinn_zeiten and random.random() < gewinnchance:
            gewonnener_preis = reduce_prize(prizes, heute.day)
            if not gewonnener_preis or not gewonnener_preis.get("name"):
                if DEBUG: logging.debug("Preis konnte nicht reduziert werden")
                hinweis = "Alle Preise wurden bereits vergeben."
                if tag != 24 and prizes and prizes[0].get("remaining", 0) > 0:
                    hinweis = (
                        "Alle heutigen Preise wurden bereits vergeben. "
                        "Der Hauptpreis wird erst am 24. Dezember verlost."
                    )
                return make_response(render_template_string(GENERIC_PAGE, content=hinweis))
            aktuelles_jahr = heute.year
            preis_name = gewonnener_preis.get("name", "")
            sponsor_name = str(gewonnener_preis.get("sponsor", "") or "").strip()
            sponsor_link = str(gewonnener_preis.get("sponsor_link", "") or "").strip()
            speichere_gewinner(user_id, display_name, tag, preis_name, jahr=aktuelles_jahr, sponsor=sponsor_name)
            qr_content = f"{tag}-{display_name}-{user_id}-{preis_name}-OV L11-{aktuelles_jahr}"
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(qr_content)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            qr_filename = f"user_{user_id}_{tag}.png"
            os.makedirs('qr_codes', exist_ok=True)
            img.save(os.path.join('qr_codes', qr_filename))  # Speicherort korrigiert
            if DEBUG: logging.debug(f"QR-Code generiert und gespeichert: {qr_filename}")
            record_user_reward(
                user_id,
                tag,
                preis_name,
                sponsor=sponsor_name,
                sponsor_link=sponsor_link,
                qr_filename=qr_filename,
                qr_content=qr_content,
            )
            prize_label = escape(preis_name)
            sponsor_hint = Markup("")
            if sponsor_name:
                escaped_sponsor_name = escape(sponsor_name)
                if sponsor_link:
                    escaped_sponsor_link = escape(sponsor_link)
                    sponsor_hint = Markup(
                        " – Sponsor: "
                        f"<a href=\"{escaped_sponsor_link}\" target=\"_blank\" rel=\"noopener noreferrer\">"
                        f"{escaped_sponsor_name}</a>"
                    )
                else:
                    sponsor_hint = Markup(f" – Sponsor: {escaped_sponsor_name}")
            qr_filename_escaped = escape(qr_filename)
            content = Markup(
                f"Glückwunsch, {safe_display_name}! Du hast {prize_label}{sponsor_hint} gewonnen. "
                f"<a href='/download_qr/{qr_filename_escaped}'>Lade deinen QR-Code herunter</a> "
                f"oder sieh ihn dir <a href='/qr_codes/{qr_filename_escaped}'>hier an</a>."
            )
            return make_response(render_template_string(GENERIC_PAGE, content=content))
        else:
            if DEBUG:
                logging.debug("Kein Gewinn für Benutzer %s am Tag %s", user_id, tag)
            return make_response(
                render_template_string(
                    GENERIC_PAGE,
                    content=f"Du hattest heute leider kein Glück, {safe_display_name}. Versuche es morgen noch einmal!",
                )
            )
    else:
        if DEBUG: logging.debug(f"Türchen {tag} kann heute noch nicht geöffnet werden")
        return make_response(render_template_string(GENERIC_PAGE, content="Dieses Türchen kann heute noch nicht geöffnet werden."))

@app.route('/download_qr/<filename>', methods=['GET'])
def download_qr(filename):
    if DEBUG: logging.debug(f"Download-Anfrage für QR-Code: {filename}")
    if not session.get('user_id'):
        return redirect(url_for('login'))
    return send_from_directory('qr_codes', filename, as_attachment=True)

@app.route('/qr_codes/<filename>')
def qr_code(filename):
    if not session.get('user_id'):
        return redirect(url_for('login'))
    return send_from_directory('qr_codes', filename)

# Route für das Ausliefern von Event-Graphen hinzufügen
@app.route('/event_graphen/<filename>')
def event_graph(filename):
    return send_from_directory('event_graphen', filename)

# HTML-Templates mit Header und Footer
HOME_PAGE = '''
<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Adventskalender</title>
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Mountains+of+Christmas:wght@400;700&family=Open+Sans:wght@400;600&display=swap');
      * { box-sizing: border-box; }
      body {
        font-family: 'Open Sans', Arial, sans-serif;
        margin: 0;
        min-height: 100vh;
        color: #f8f9fa;
        background: linear-gradient(180deg, #0b1d2b 0%, #12324a 50%, #1c5560 100%);
        position: relative;
        overflow-x: hidden;
        padding-bottom: calc(var(--footer-height, 160px) + 40px);
      }
      body::before,
      body::after {
        content: none;
      }
      @keyframes snowDrift {
        from { transform: translate3d(0, 0, 0); }
        to { transform: translate3d(-180px, 0, 0); }
      }
      @keyframes wheelSpin {
        from { transform: translate(-50%, -50%) rotate(0deg); }
        to { transform: translate(-50%, -50%) rotate(360deg); }
      }
      @keyframes snowPlowBeaconPulse {
        0%, 18%, 100% {
          opacity: 0.3;
          transform: scale(0.86);
          box-shadow: 0 0 6px rgba(255, 216, 111, 0.45),
                      0 0 12px rgba(255, 216, 111, 0.28);
        }
        26% {
          opacity: 0.75;
          transform: scale(0.98);
          box-shadow: 0 0 10px rgba(255, 216, 111, 0.6),
                      0 0 18px rgba(255, 216, 111, 0.55);
        }
        46% {
          opacity: 1;
          transform: scale(1.06);
          box-shadow: 0 0 14px rgba(255, 216, 111, 0.85),
                      0 0 32px rgba(255, 216, 111, 0.95);
        }
        62% {
          opacity: 0.78;
          transform: scale(0.97);
          box-shadow: 0 0 8px rgba(255, 216, 111, 0.58),
                      0 0 22px rgba(255, 216, 111, 0.62);
        }
        82% {
          opacity: 0.52;
          transform: scale(0.9);
          box-shadow: 0 0 6px rgba(255, 216, 111, 0.48),
                      0 0 14px rgba(255, 216, 111, 0.42);
        }
      }
      @keyframes snowPlowBeaconHalo {
        0%, 18%, 100% {
          opacity: 0;
          transform: scale(0.55);
        }
        30% {
          opacity: 0.35;
          transform: scale(0.8);
        }
        46% {
          opacity: 0.95;
          transform: scale(1.08);
        }
        68% {
          opacity: 0.4;
          transform: scale(0.88);
        }
      }
      @keyframes snowPlowBeaconBeam {
        0%, 100% {
          opacity: 0;
          transform: rotate(-6deg) scaleY(0.6);
        }
        20% {
          opacity: 0.35;
          transform: rotate(-3deg) scaleY(0.85);
        }
        46% {
          opacity: 0.8;
          transform: rotate(4deg) scaleY(1);
        }
        70% {
          opacity: 0.45;
          transform: rotate(-2deg) scaleY(0.75);
        }
      }
      @keyframes snowPlowBladeReflection {
        0%, 100% {
          opacity: 0.25;
          transform: translateX(-12px);
        }
        40% {
          opacity: 0.6;
          transform: translateX(12px);
        }
        60% {
          opacity: 0.8;
          transform: translateX(28px);
        }
        85% {
          opacity: 0.4;
          transform: translateX(-6px);
        }
      }
      #snow-canvas {
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100vh;
        pointer-events: none;
        z-index: 2;
      }
      #snow-plow {
        position: fixed;
        left: 0;
        bottom: calc(var(--footer-height, 160px) - 32px);
        width: 200px;
        height: 110px;
        pointer-events: none;
        z-index: 3;
        opacity: 0;
        transition: opacity 0.35s ease, filter 0.35s ease;
        transform: translate3d(-240px, 0, 0);
        will-change: transform;
        transform-origin: 90px 92px;
        filter: drop-shadow(0 10px 18px rgba(0, 0, 0, 0.45));
      }
      #snow-plow.is-active {
        opacity: 1;
        filter: drop-shadow(0 14px 22px rgba(0, 0, 0, 0.55));
      }
      .snow-plow__body {
        position: absolute;
        bottom: 30px;
        left: 48px;
        width: 118px;
        height: 54px;
        background: linear-gradient(135deg, #fca326 0%, #ffbf45 32%, #ff9825 68%, #ffd86f 100%);
        border-radius: 12px 16px 18px 12px;
        box-shadow: 0 8px 18px rgba(0, 0, 0, 0.38);
        border: 1px solid rgba(255, 240, 214, 0.45);
        overflow: hidden;
        z-index: 5;
      }
      .snow-plow__body::before {
        content: "";
        position: absolute;
        inset: 6px 8px 10px 10px;
        border-radius: 10px 12px 14px 10px;
        background: linear-gradient(125deg, rgba(255, 221, 144, 0.55) 0%, rgba(255, 166, 54, 0.35) 32%, rgba(210, 98, 21, 0.4) 72%, rgba(70, 40, 12, 0.45) 100%);
        box-shadow: inset 0 0 18px rgba(0, 0, 0, 0.28);
        opacity: 0.85;
      }
      .snow-plow__body::after {
        content: "";
        position: absolute;
        inset: 12px 14px 16px 18px;
        border-radius: 8px;
        background: radial-gradient(circle at 22% 38%, rgba(255, 255, 255, 0.32) 0%, rgba(255, 255, 255, 0) 60%),
                    radial-gradient(circle at 78% 70%, rgba(105, 65, 18, 0.35) 0%, rgba(105, 65, 18, 0) 72%),
                    repeating-linear-gradient(0deg, rgba(90, 52, 10, 0.18) 0 6px, rgba(255, 216, 140, 0.08) 6px 12px);
        opacity: 0.65;
        mix-blend-mode: multiply;
      }
      .snow-plow__cabin {
        position: absolute;
        top: -30px;
        left: 16px;
        width: 60px;
        height: 40px;
        background: linear-gradient(140deg, #ffe8af 0%, #fff7d3 45%, #fcd077 100%);
        border-radius: 12px 12px 8px 8px;
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.25);
        border: 1px solid rgba(255, 240, 206, 0.65);
        overflow: hidden;
      }
      .snow-plow__cabin::before {
        content: "";
        position: absolute;
        left: 6px;
        right: 6px;
        bottom: 6px;
        height: 10px;
        border-radius: 6px;
        background: linear-gradient(180deg, rgba(180, 102, 18, 0.55), rgba(60, 34, 10, 0.4));
        opacity: 0.6;
      }
      .snow-plow__cabin::after {
        content: "";
        position: absolute;
        inset: 4px 8px 8px 10px;
        border-radius: 8px;
        background: radial-gradient(circle at 20% 30%, rgba(255, 255, 255, 0.5) 0%, rgba(255, 255, 255, 0) 70%),
                    radial-gradient(circle at 88% 82%, rgba(180, 108, 28, 0.35) 0%, rgba(180, 108, 28, 0) 80%);
        opacity: 0.5;
      }
      .snow-plow__window {
        position: absolute;
        top: 8px;
        left: 10px;
        width: 40px;
        height: 20px;
        background: linear-gradient(120deg, rgba(255, 255, 255, 0.95) 0%, rgba(198, 229, 255, 0.82) 45%, rgba(78, 132, 172, 0.55) 100%);
        border-radius: 6px;
        box-shadow: inset 0 0 8px rgba(255, 255, 255, 0.85);
        overflow: hidden;
      }
      .snow-plow__window::before {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(135deg, rgba(255, 255, 255, 0.9) 0%, rgba(255, 255, 255, 0) 55%);
        transform: skewX(-12deg) translateX(-6px);
        opacity: 0.85;
      }
      .snow-plow__window::after {
        content: "";
        position: absolute;
        inset: -6px -4px 4px -4px;
        background: radial-gradient(circle at 80% 10%, rgba(255, 255, 255, 0.3) 0%, rgba(255, 255, 255, 0) 60%),
                    radial-gradient(circle at 12% 92%, rgba(90, 126, 158, 0.35) 0%, rgba(90, 126, 158, 0) 70%);
        mix-blend-mode: screen;
      }
      .snow-plow__light {
        position: absolute;
        top: -10px;
        right: -12px;
        width: 18px;
        height: 18px;
        background: radial-gradient(circle at 30% 30%, #fff7d3 0%, #ffd86f 45%, rgba(255, 216, 111, 0.05) 100%);
        border-radius: 50%;
        box-shadow: 0 0 14px rgba(255, 216, 111, 0.8), 0 0 24px rgba(255, 203, 72, 0.6);
        animation: snowPlowBeaconPulse 1.2s infinite ease-in-out;
        overflow: visible;
      }
      .snow-plow__light::before {
        content: "";
        position: absolute;
        top: 50%;
        right: 18px;
        width: 76px;
        height: 48px;
        background: radial-gradient(circle at left, rgba(255, 210, 120, 0.48) 0%, rgba(255, 210, 120, 0.12) 55%, rgba(255, 210, 120, 0) 80%);
        border-radius: 100% 0 0 100% / 70% 0 0 70%;
        transform-origin: right center;
        opacity: 0;
        pointer-events: none;
      }
      #snow-plow.is-active .snow-plow__light::before {
        animation: snowPlowBeaconBeam 1.2s infinite ease-in-out;
      }
      .snow-plow__light::after {
        content: "";
        position: absolute;
        inset: -20px;
        border-radius: 50%;
        background: radial-gradient(circle, rgba(255, 216, 111, 0.55) 0%, rgba(255, 216, 111, 0) 70%);
        opacity: 0;
        animation: snowPlowBeaconHalo 1.2s infinite ease-out;
      }
      .snow-plow__blade {
        position: absolute;
        bottom: 20px;
        left: -26px;
        width: 90px;
        height: 46px;
        background: linear-gradient(130deg, #ffb13e 0%, #ff9122 35%, #ff7b14 70%, #ffce66 100%);
        border-radius: 14px;
        transform: skewX(-18deg);
        box-shadow: 0 12px 22px rgba(0, 0, 0, 0.42);
        border: 1px solid rgba(255, 240, 214, 0.35);
        overflow: hidden;
        z-index: 6;
      }
      .snow-plow__blade::before {
        content: "";
        position: absolute;
        inset: 6px 10px 10px 10px;
        border-radius: 10px;
        background: repeating-linear-gradient(135deg, rgba(34, 20, 4, 0.82) 0 14px, rgba(255, 215, 92, 0.88) 14px 28px);
        opacity: 0.72;
        mix-blend-mode: multiply;
      }
      .snow-plow__blade::after {
        content: "";
        position: absolute;
        inset: 6px;
        border-radius: 10px;
        border: 1px solid rgba(255, 255, 255, 0.35);
        box-shadow: inset 0 0 10px rgba(255, 255, 255, 0.15);
      }
      #snow-plow.is-active .snow-plow__blade::before {
        animation: snowPlowBladeReflection 1.6s infinite ease-in-out;
      }
      .snow-plow__wheel {
        position: absolute;
        bottom: 0;
        width: 42px;
        height: 42px;
        background: radial-gradient(circle at 32% 30%, rgba(255, 255, 255, 0.12) 0%, rgba(27, 39, 53, 0.95) 55%, rgba(11, 16, 24, 1) 100%);
        border-radius: 50%;
        box-shadow: inset 0 0 0 6px #ffcf5c, inset 0 0 14px rgba(0, 0, 0, 0.55), 0 4px 10px rgba(0, 0, 0, 0.5);
        overflow: hidden;
        position: relative;
        z-index: 2;
      }
      .snow-plow__wheel--left { left: 48px; }
      .snow-plow__wheel--right { left: 128px; }
      .snow-plow__wheel::before {
        content: "";
        position: absolute;
        inset: 6px;
        border-radius: 50%;
        background: repeating-linear-gradient(45deg, rgba(17, 24, 34, 0.85) 0 4px, rgba(48, 62, 82, 0.85) 4px 8px);
        opacity: 0.45;
        mix-blend-mode: multiply;
      }
      .snow-plow__wheel::after {
        content: "";
        position: absolute;
        inset: 10px;
        border-radius: 50%;
        border: 3px solid rgba(255, 207, 92, 0.8);
        box-shadow: inset 0 0 10px rgba(255, 207, 92, 0.25);
      }
      .snow-plow__wheel span {
        position: absolute;
        top: 50%;
        left: 50%;
        width: 6px;
        height: 32px;
        background: linear-gradient(180deg, rgba(255, 210, 110, 0.95), rgba(147, 91, 14, 0.85));
        border-radius: 3px;
        transform: translate(-50%, -50%);
        box-shadow: 0 0 6px rgba(255, 210, 110, 0.55);
      }
      .snow-plow__wheel span::before {
        content: "";
        position: absolute;
        top: 50%;
        left: 50%;
        width: 18px;
        height: 6px;
        background: inherit;
        border-radius: 3px;
        transform: translate(-50%, -50%) rotate(90deg);
      }
      #snow-plow.is-active .snow-plow__wheel span {
        animation: wheelSpin 1s linear infinite;
      }
      .snow-plow__fender {
        position: absolute;
        bottom: 42px;
        width: 58px;
        height: 28px;
        background: linear-gradient(180deg, rgba(255, 213, 118, 0.95), rgba(186, 110, 28, 0.88));
        border-radius: 50% 50% 40% 40%;
        box-shadow: inset 0 4px 8px rgba(255, 255, 255, 0.35), 0 4px 6px rgba(0, 0, 0, 0.28);
        z-index: 4;
      }
      .snow-plow__fender::before {
        content: "";
        position: absolute;
        inset: 6px 10px;
        border-radius: 50% 50% 38% 38%;
        background: radial-gradient(circle at 30% 20%, rgba(255, 255, 255, 0.35) 0%, rgba(255, 255, 255, 0) 60%),
                    linear-gradient(180deg, rgba(150, 86, 18, 0.45), rgba(55, 30, 6, 0.45));
        opacity: 0.75;
      }
      .snow-plow__fender--left { left: 40px; }
      .snow-plow__fender--right { left: 120px; }
      .snow-plow__mirror {
        position: absolute;
        top: -18px;
        width: 18px;
        height: 26px;
        background: linear-gradient(135deg, rgba(235, 242, 252, 0.92), rgba(118, 152, 180, 0.82));
        border-radius: 6px;
        border: 2px solid rgba(38, 50, 68, 0.85);
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.25);
        pointer-events: none;
        z-index: 7;
      }
      .snow-plow__mirror::before {
        content: "";
        position: absolute;
        bottom: -10px;
        left: 6px;
        width: 4px;
        height: 16px;
        background: linear-gradient(180deg, rgba(48, 58, 72, 0.9), rgba(18, 24, 34, 0.9));
        border-radius: 2px;
      }
      .snow-plow__mirror::after {
        content: "";
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        border-radius: inherit;
        background: linear-gradient(135deg, rgba(255, 255, 255, 0.75), rgba(255, 255, 255, 0));
        mix-blend-mode: screen;
        opacity: 0.7;
      }
      .snow-plow__mirror--left {
        left: -20px;
        transform: rotate(-10deg);
        transform-origin: 100% 50%;
      }
      .snow-plow__mirror--right {
        right: -26px;
        transform: rotate(12deg);
        transform-origin: 0 50%;
      }
      .snow-plow__exhaust {
        position: absolute;
        top: -18px;
        left: 6px;
        width: 14px;
        height: 46px;
        background: linear-gradient(180deg, rgba(94, 104, 118, 0.95), rgba(36, 42, 52, 1));
        border-radius: 6px 6px 8px 8px;
        box-shadow: inset 0 0 8px rgba(255, 255, 255, 0.12), 0 4px 6px rgba(0, 0, 0, 0.3);
        z-index: 6;
      }
      .snow-plow__exhaust::after {
        content: "";
        position: absolute;
        top: -10px;
        left: 2px;
        width: 10px;
        height: 10px;
        background: linear-gradient(180deg, rgba(190, 196, 204, 0.95), rgba(74, 80, 92, 0.9));
        border-radius: 50% 50% 40% 40%;
        box-shadow: inset 0 2px 4px rgba(255, 255, 255, 0.4);
      }
      .snow-plow__step {
        position: absolute;
        bottom: 12px;
        width: 28px;
        height: 6px;
        background: linear-gradient(180deg, rgba(78, 92, 104, 0.95), rgba(18, 24, 30, 0.95));
        border-radius: 4px;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.35);
        overflow: hidden;
        z-index: 3;
      }
      .snow-plow__step::before {
        content: "";
        position: absolute;
        inset: 0;
        background: repeating-linear-gradient(90deg, rgba(20, 24, 28, 0.85) 0 4px, rgba(150, 168, 186, 0.65) 4px 6px);
        opacity: 0.75;
      }
      .snow-plow__step--left { left: 70px; }
      .snow-plow__step--right { left: 102px; }
      .snow-ground {
        position: fixed;
        left: 0;
        bottom: 0;
        width: 100%;
        height: var(--footer-height, 160px);
        pointer-events: none;
        background: linear-gradient(180deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.22) 30%, rgba(255,255,255,0.85) 100%);
        box-shadow: 0 -28px 48px rgba(255, 255, 255, 0.18);
        overflow: hidden;
        z-index: 1;
      }
      .snow-ground::before,
      .snow-ground::after {
        content: none;
      }
      header, footer {
        padding: 18px;
        background: #0f2e48;
        text-align: center;
        border-bottom: 2px solid rgba(255, 255, 255, 0.2);
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.4);
      }
      footer {
        border-bottom: none;
        border-top: 2px solid rgba(255, 255, 255, 0.2);
        position: fixed;
        bottom: 0;
        left: 0;
        width: 100%;
        display: flex;
        justify-content: center;
        align-items: center;
        z-index: 3;
      }
      .footer-inner {
        width: min(100%, 960px);
        margin: 0 auto;
        text-align: center;
        padding: 0 24px;
      }
      nav {
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        align-items: center;
        gap: 12px;
      }
      nav a {
        margin: 0 10px;
        color: #ffeecf;
        text-decoration: none;
        font-weight: 600;
        transition: color 0.3s ease;
      }
      nav a:hover {
        color: #ffcf5c;
      }
      .nav-user {
        display: flex;
        align-items: center;
        gap: 10px;
        color: #ffeecf;
        font-weight: 600;
      }
      .admin-button,
      .logout-button,
      .auth-button {
        padding: 8px 14px;
        border-radius: 6px;
        border: none;
        background: linear-gradient(135deg, #f87171, #fbbf24);
        color: #1b1b1b;
        font-weight: 700;
        text-decoration: none;
        text-transform: uppercase;
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.25);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }
      .admin-button:hover,
      .logout-button:hover,
      .auth-button:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 14px rgba(0, 0, 0, 0.3);
      }
      .admin-button {
        background: linear-gradient(135deg, #34d399, #22d3ee);
      }
      .auth-button {
        background: linear-gradient(135deg, #60a5fa, #a855f7);
        color: #0b1d2b;
      }
      .auth-button.register {
        background: linear-gradient(135deg, #fbbf24, #f97316);
        color: #1b1b1b;
      }
      .preise {
        margin-top: 10px;
        font-weight: 600;
        color: #ffcf5c;
      }
      .intro-grid {
        display: grid;
        gap: 20px;
        max-width: 960px;
        margin: 0 auto 35px;
      }
      @media (min-width: 768px) {
        .intro-grid {
          grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.15fr);
        }
      }
      .intro-card {
        background: rgba(12, 35, 52, 0.75);
        border-radius: 16px;
        padding: 20px 24px;
        box-shadow: 0 10px 26px rgba(0, 0, 0, 0.35);
        border: 1px solid rgba(255, 255, 255, 0.15);
        text-align: center;
      }
      .intro-card p {
        background: transparent;
        box-shadow: none;
        margin-bottom: 12px;
      }
      .intro-card h2 {
        margin-top: 0;
        font-family: 'Mountains of Christmas', 'Open Sans', cursive;
        font-size: 1.8rem;
        color: #ffcf5c;
      }
      .countdown-circle {
        width: 120px;
        height: 120px;
        margin: 0 auto 15px;
        border-radius: 50%;
        background: radial-gradient(circle at 30% 30%, #ffcf5c, #ff7b7b);
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        color: #12263f;
        font-weight: 700;
        box-shadow: 0 14px 28px rgba(0, 0, 0, 0.4);
      }
      .countdown-circle span {
        font-size: 2.2rem;
        line-height: 1;
      }
      .countdown-text {
        margin: 0;
        color: #ffeecf;
        font-weight: 600;
      }
      .prize-list {
        list-style: none;
        padding: 0;
        margin: 0;
        display: flex;
        flex-direction: column;
        gap: 12px;
        align-items: stretch;
      }
      .prize-list li {
        flex: 1 1 auto;
        min-width: 0;
        width: 100%;
        padding: 10px 16px;
        border-radius: 12px;
        background: rgba(17, 45, 68, 0.75);
        color: #ffeecf;
        font-weight: 600;
        text-align: left;
        white-space: normal;
        word-break: break-word;
      }
      .sponsor-highlight {
        max-width: 960px;
        margin: 40px auto 50px;
        padding: 28px 30px;
        border-radius: 20px;
        background: linear-gradient(135deg, rgba(255, 207, 92, 0.18), rgba(52, 211, 153, 0.16));
        border: 1px solid rgba(255, 255, 255, 0.25);
        box-shadow: 0 18px 32px rgba(0, 0, 0, 0.35);
      }
      .sponsor-highlight h2 {
        margin-top: 0;
        margin-bottom: 18px;
        text-align: center;
        color: #ffcf5c;
        font-family: 'Mountains of Christmas', 'Open Sans', cursive;
        font-size: 2rem;
      }
      .sponsor-highlight p {
        background: transparent;
        margin-bottom: 20px;
      }
      .sponsor-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 20px;
      }
      .sponsor-card {
        position: relative;
        padding: 20px 22px 18px;
        border-radius: 18px;
        background: radial-gradient(circle at 20% 20%, rgba(255, 207, 92, 0.25), rgba(11, 29, 43, 0.9));
        border: 1px solid rgba(255, 255, 255, 0.18);
        box-shadow: 0 18px 28px rgba(0, 0, 0, 0.45);
        display: flex;
        flex-direction: column;
        gap: 14px;
        min-height: 160px;
        overflow: hidden;
        isolation: isolate;
        transition: transform 0.25s ease, box-shadow 0.25s ease;
      }
      .sponsor-card::before {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(140deg, rgba(255, 255, 255, 0.18), transparent 55%);
        opacity: 0.7;
        transition: opacity 0.3s ease;
        pointer-events: none;
      }
      .sponsor-card:hover {
        transform: translateY(-6px);
        box-shadow: 0 22px 38px rgba(0, 0, 0, 0.55);
      }
      .sponsor-card:hover::before {
        opacity: 0.95;
      }
      .sponsor-card__header {
        display: flex;
        align-items: center;
        gap: 12px;
      }
      .sponsor-card__icon {
        width: 48px;
        height: 48px;
        border-radius: 16px;
        background: linear-gradient(135deg, rgba(255, 207, 92, 0.95), rgba(255, 125, 125, 0.85));
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 1.6rem;
        color: #102030;
        box-shadow: 0 10px 16px rgba(0, 0, 0, 0.3);
      }
      .sponsor-card__name {
        margin: 0;
        color: #ffefc2;
        font-weight: 700;
        font-size: 1.15rem;
        text-align: left;
      }
      .sponsor-card__details {
        margin: 0;
        padding: 0;
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .sponsor-card__item {
        display: flex;
        flex-direction: column;
        gap: 4px;
        background: rgba(6, 20, 32, 0.6);
        border-radius: 12px;
        padding: 10px 12px;
        box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.06);
      }
      .sponsor-card__label {
        font-size: 0.85rem;
        color: rgba(217, 243, 255, 0.8);
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }
      .sponsor-card__link,
      .sponsor-card__text {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        font-weight: 600;
        font-size: 0.95rem;
        color: #d9f3ff;
        text-decoration: none;
        word-break: break-word;
      }
      .sponsor-card__link::after {
        content: "↗";
        font-size: 0.8em;
      }
      .sponsor-card__link:hover {
        color: #8de0ff;
        text-decoration: underline;
      }
      .rewards-section {
        max-width: 960px;
        margin: 40px auto 50px;
        background: rgba(12, 35, 52, 0.78);
        border-radius: 18px;
        padding: 26px 30px;
        box-shadow: 0 18px 36px rgba(0, 0, 0, 0.45);
        border: 1px solid rgba(255, 255, 255, 0.18);
      }
      .rewards-section h2 {
        font-family: 'Mountains of Christmas', 'Open Sans', cursive;
        font-size: 2rem;
        margin: 0 0 16px;
        color: #ffcf5c;
        text-align: center;
      }
      .reward-list {
        display: grid;
        gap: 16px;
        grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
        margin-top: 20px;
      }
      .reward-card {
        background: rgba(9, 26, 40, 0.85);
        border-radius: 16px;
        padding: 18px 20px;
        border: 1px solid rgba(255, 255, 255, 0.12);
        box-shadow: 0 14px 28px rgba(0, 0, 0, 0.45);
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .reward-card strong {
        font-size: 1.1rem;
        color: #ffcf5c;
      }
      .reward-meta {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        font-size: 0.95rem;
        color: #ffeecf;
        gap: 8px;
        flex-wrap: wrap;
      }
      .reward-meta span {
        display: inline-flex;
        align-items: center;
        gap: 6px;
      }
      .reward-sponsor {
        font-size: 0.9rem;
        color: #d9f3ff;
      }
      .reward-actions {
        margin-top: auto;
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
      }
      .reward-actions a {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 8px 14px;
        border-radius: 10px;
        background: linear-gradient(135deg, #60a5fa, #34d399);
        color: #0b1d2b;
        font-weight: 700;
        text-decoration: none;
        box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }
      .reward-actions a:hover {
        transform: translateY(-2px);
        box-shadow: 0 10px 22px rgba(0, 0, 0, 0.4);
      }
      .reward-empty {
        text-align: center;
        color: #ffeecf;
        font-weight: 600;
        margin: 12px 0 0;
      }
      main {
        padding: 30px 20px calc(var(--footer-height, 160px) + 40px);
        position: relative;
        z-index: 3;
      }
      h1 {
        font-family: 'Mountains of Christmas', 'Open Sans', cursive;
        font-size: 2.8rem;
        text-align: center;
        margin: 30px auto 10px;
        text-shadow: 0 3px 6px rgba(0, 0, 0, 0.6);
        letter-spacing: 1px;
      }
      p {
        text-align: center;
        max-width: 720px;
        margin: 0 auto 25px;
        line-height: 1.6;
        background: rgba(12, 35, 52, 0.7);
        padding: 12px 20px;
        border-radius: 12px;
        box-shadow: 0 6px 20px rgba(0, 0, 0, 0.3);
      }
      form {
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 12px;
        flex-wrap: wrap;
        margin-bottom: 35px;
      }
      label {
        font-weight: 600;
      }
      input[type="text"] {
        padding: 10px 14px;
        border-radius: 8px;
        border: none;
        width: 240px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
      }
      button {
        padding: 10px 18px;
        border-radius: 8px;
        border: none;
        background: linear-gradient(135deg, #ff7b7b, #ffcf5c);
        color: #1b1b1b;
        font-weight: 700;
        cursor: pointer;
        text-transform: uppercase;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }
      button:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 15px rgba(0, 0, 0, 0.35);
      }
      .inactive-banner {
        max-width: 760px;
        margin: 0 auto 25px;
        padding: 16px 22px;
        border-radius: 16px;
        border: 2px solid rgba(255, 166, 166, 0.65);
        background: rgba(220, 53, 69, 0.55);
        color: #fff5f5;
        font-weight: 600;
        text-align: center;
        box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
      }
      .welcome {
        text-align: center;
        font-size: 1.2rem;
        margin-bottom: 20px;
        color: #ffeecf;
      }
      .calendar-board {
        position: relative;
        margin: 30px auto 80px;
        padding: 30px 28px 36px;
        max-width: 760px;
        background: linear-gradient(120deg, rgba(82, 45, 26, 0.92), rgba(53, 30, 18, 0.96));
        border: 12px solid #d9b26f;
        border-radius: 28px;
        box-shadow: 0 22px 45px rgba(0, 0, 0, 0.45);
        overflow: hidden;
      }
      .calendar-board::before {
        content: "";
        position: absolute;
        inset: 0;
        background-image: repeating-linear-gradient(
          115deg,
          rgba(255, 255, 255, 0.06) 0px,
          rgba(255, 255, 255, 0.06) 12px,
          transparent 12px,
          transparent 26px
        );
        mix-blend-mode: soft-light;
        opacity: 0.7;
        pointer-events: none;
      }
      .calendar-header {
        position: relative;
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 22px;
        padding: 12px 20px;
        border-radius: 18px;
        background: linear-gradient(135deg, rgba(255, 226, 169, 0.9), rgba(255, 189, 111, 0.85));
        color: #53271d;
        font-family: 'Mountains of Christmas', 'Open Sans', cursive;
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8), 0 12px 26px rgba(0, 0, 0, 0.25);
        text-transform: uppercase;
        letter-spacing: 2px;
      }
      .calendar-header span {
        font-size: 1.4rem;
      }
      .tuerchen-container {
        position: relative;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
        gap: 18px;
        justify-items: center;
        perspective: 900px;
      }
      .tuerchen {
        position: relative;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 110px;
        height: 110px;
        border-radius: 14px;
        font-size: 28px;
        font-weight: 700;
        color: #2f180f;
        text-decoration: none;
        border: 3px solid rgba(255, 242, 214, 0.7);
        box-shadow: 0 18px 28px rgba(0, 0, 0, 0.45);
        transition: transform 0.35s ease, box-shadow 0.35s ease;
        background: linear-gradient(150deg, rgba(255, 255, 255, 0.85), var(--door-color, #ffd27f));
        transform-origin: left center;
        transform-style: preserve-3d;
        overflow: hidden;
      }
      .tuerchen::before {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(120deg, rgba(255, 255, 255, 0.55), rgba(255, 255, 255, 0));
        opacity: 0.9;
        pointer-events: none;
      }
      .tuerchen::after {
        content: "";
        position: absolute;
        right: 14px;
        top: 50%;
        transform: translateY(-50%);
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: radial-gradient(circle at 30% 30%, #ffe7b4, #d9a23d);
        box-shadow: 0 0 6px rgba(255, 220, 150, 0.8);
      }
      .tuerchen .door-number {
        position: relative;
        z-index: 1;
        text-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
        letter-spacing: 1px;
      }
      .tuerchen:hover {
        transform: rotateY(-14deg) translateY(-4px);
        box-shadow: 0 24px 36px rgba(0, 0, 0, 0.55);
      }
      .tuerchen.current-day {
        box-shadow: 0 26px 38px rgba(255, 220, 150, 0.55), 0 18px 28px rgba(0, 0, 0, 0.45);
        animation: door-glow 1.6s ease-in-out infinite;
      }
      @keyframes door-glow {
        0%, 100% {
          filter: brightness(1);
        }
        50% {
          filter: brightness(1.15);
        }
      }
      .disabled {
        pointer-events: none;
        cursor: default;
        transform: rotateY(-30deg) translateX(6px);
        box-shadow: 0 18px 28px rgba(0, 0, 0, 0.25), inset 0 0 35px rgba(255, 255, 255, 0.35);
        background: linear-gradient(145deg, rgba(255, 239, 217, 0.95), rgba(249, 206, 151, 0.85));
      }
      .disabled::before {
        opacity: 0.35;
      }
      .disabled::after {
        opacity: 0;
      }
      .disabled .door-number {
        color: #a2552f;
        text-shadow: none;
      }
      footer p {
        margin: 0;
        color: #ffeecf;
      }
    </style>
  </head>
  <body>
    <header>
      <nav>
        <a href="/">Zurück zum Adventskalender</a>
        <div class="preise">Verbleibende Preise: {{ verbleibende_preise }} von {{ max_preise }}</div>
        <div class="nav-user">
          {% if is_logged_in %}
            <span>Angemeldet als {{ username }}</span>
            {% if is_admin and admin_url %}
            <a class="admin-button" href="{{ admin_url }}">Adminbereich</a>
            {% endif %}
            <a class="logout-button" href="{{ logout_url }}">Logout</a>
          {% else %}
            <span>Nicht angemeldet</span>
            <a class="auth-button" href="{{ login_url }}">Login</a>
            <a class="auth-button register" href="{{ register_url }}">Registrieren</a>
          {% endif %}
        </div>
      </nav>
    </header>
    <main>
      {% if not calendar_active %}
      <div class="inactive-banner">
        <strong>Kalender pausiert:</strong> Der Adventskalender ist derzeit deaktiviert. Schau bald wieder vorbei!
      </div>
      {% endif %}
      <h1>Adventskalender des OV L11</h1>
      <p>Stell jeden Tag ein neues Türchen frei, genieße die winterliche Vorfreude und sichere dir mit etwas Glück {% if prize_phrase %}einen unserer festlichen Preise wie {{ prize_phrase }}{% else %}einen festlichen Preis{% endif %} in unserer festlich geschmückten Clubstation! Die Preisvergabe findet am ersten OV-Abend am 13.01.2026 um 19 Uhr statt.</p>
      {% if sponsors %}
      <section class="sponsor-highlight">
        <h2>Unsere Sponsoren</h2>
        <p>Ein herzliches Dankeschön an diese Unterstützer, die unsere Preise ermöglichen:</p>
        <div class="sponsor-grid">
          {% for sponsor in sponsors %}
            <article class="sponsor-card">
              <div class="sponsor-card__header">
                <div class="sponsor-card__icon" aria-hidden="true">🎁</div>
                <h3 class="sponsor-card__name">{{ sponsor.name }}</h3>
              </div>
              {% if sponsor.links %}
                <ul class="sponsor-card__details">
                  {% for link in sponsor.links %}
                    <li class="sponsor-card__item">
                      {% if link.url %}
                        <a class="sponsor-card__link" href="{{ link.url }}" target="_blank" rel="noopener noreferrer">{{ link.label }}</a>
                      {% else %}
                        <span class="sponsor-card__text">{{ link.label }}</span>
                      {% endif %}
                    </li>
                  {% endfor %}
                </ul>
              {% endif %}
            </article>
          {% endfor %}
        </div>
      </section>
      {% endif %}
      <section class="intro-grid">
        <div class="intro-card">
          <h2>Countdown bis Heiligabend</h2>
          <div class="countdown-circle">
            {% if tage_bis_weihnachten > 0 %}
              <span>{{ tage_bis_weihnachten }}</span>
              <small>{% if tage_bis_weihnachten == 1 %}Tag{% else %}Tage{% endif %}</small>
            {% else %}
              <span>🎄</span>
            {% endif %}
          </div>
          <p class="countdown-text">
            {% if tage_bis_weihnachten > 0 %}
              Noch {{ tage_bis_weihnachten }} {% if tage_bis_weihnachten == 1 %}Tag{% else %}Tage{% endif %} voller Vorfreude bis Heiligabend.
            {% else %}
              Frohe Weihnachten! Alle Türchen sind geöffnet – genieße die festliche Zeit.
            {% endif %}
          </p>
        </div>
        <div class="intro-card">
          <h2>Festliche Gewinne</h2>
          <p class="countdown-text">Diese Überraschungen warten auf dich:</p>
          {% if prizes %}
            <ul class="prize-list">
              {% for prize in prizes %}
                <li>
                  {{ prize.name }}
                  {% if prize.total %}
                    – insgesamt {{ prize.total }}
                  {% endif %}
                  {% if prize.remaining != prize.total %}
                    (noch {{ prize.remaining }} verfügbar)
                  {% endif %}
                </li>
              {% endfor %}
            </ul>
          {% else %}
            <p class="countdown-text">Die Preise werden gerade vorbereitet. Schau bald wieder vorbei!</p>
          {% endif %}
        </div>
      </section>
      <section class="rewards-section">
        <h2>Deine Gewinne</h2>
        {% if is_logged_in %}
          {% if user_rewards %}
            <div class="reward-list">
              {% for reward in user_rewards %}
                <article class="reward-card">
                  <div class="reward-meta">
                    <span>Türchen {{ "%02d"|format(reward.door) }}</span>
                    {% if reward.display_date %}
                      <span>{{ reward.display_date }}</span>
                    {% endif %}
                  </div>
                  <strong>{{ reward.prize_name }}</strong>
                  {% if reward.sponsor %}
                    <div class="reward-sponsor">
                      Sponsor:
                      {% if reward.sponsor_link %}
                        <a href="{{ reward.sponsor_link }}" target="_blank" rel="noopener noreferrer">{{ reward.sponsor }}</a>
                      {% else %}
                        {{ reward.sponsor }}
                      {% endif %}
                    </div>
                  {% endif %}
                  {% if reward.qr_filename %}
                    <div class="reward-actions">
                      <a href="{{ url_for('qr_code', filename=reward.qr_filename) }}" target="_blank" rel="noopener noreferrer">QR anzeigen</a>
                      <a href="{{ url_for('download_qr', filename=reward.qr_filename) }}">QR herunterladen</a>
                    </div>
                  {% endif %}
                </article>
              {% endfor %}
            </div>
          {% else %}
            <p class="reward-empty">Du hast noch keinen Gewinn erzielt – wir drücken die Daumen für das nächste Türchen!</p>
          {% endif %}
        {% else %}
          <p class="reward-empty">Melde dich an oder registriere dich, um deine Gewinne zu sehen und Türchen öffnen zu können.</p>
          <div class="reward-actions">
            <a href="{{ login_url }}">Zum Login</a>
            <a href="{{ register_url }}">Jetzt registrieren</a>
          </div>
        {% endif %}
      </section>
      <div class="welcome">
        {% if is_logged_in %}
          Willkommen zurück, {{ username }}{% if user_email %} ({{ user_email }}){% endif %}! Viel Glück beim heutigen Türchen.
        {% else %}
          Willkommen beim Adventskalender! <a href="{{ login_url }}">Melde dich an</a> oder <a href="{{ register_url }}">registriere dich</a>, um mitzumachen.
        {% endif %}
        {% if not calendar_active %}<br><strong>Hinweis:</strong> Der Adventskalender ist momentan deaktiviert. Türchen können aktuell nicht geöffnet werden.{% endif %}
      </div>
      <div class="calendar-board">
        <div class="calendar-header">
          <span>Dezember</span>
          <span>{{ heute.year }}</span>
        </div>
        <div class="tuerchen-container">
          {% for num in tuerchen %}
            <a href="{% if is_logged_in and calendar_active and not tuerchen_status[num] and num >= heute.day %}/oeffne_tuerchen/{{ num }}{% elif not is_logged_in %}{{ login_url }}{% else %}#{% endif %}"
               class="tuerchen{% if not is_logged_in or not calendar_active or tuerchen_status[num] or num < heute.day %} disabled{% endif %}{% if num == heute.day %} current-day{% endif %}"
               style="--door-color: {{ tuerchen_farben[num-1] }};">
              <span class="door-number">{{ "%02d"|format(num) }}</span>
            </a>
          {% endfor %}
        </div>
      </div>
    </main>
    <canvas id="snow-canvas" aria-hidden="true"></canvas>
    <div id="snow-plow" aria-hidden="true">
      <div class="snow-plow__blade"></div>
      <div class="snow-plow__body">
        <div class="snow-plow__exhaust"></div>
        <div class="snow-plow__mirror snow-plow__mirror--left"></div>
        <div class="snow-plow__mirror snow-plow__mirror--right"></div>
        <div class="snow-plow__step snow-plow__step--left"></div>
        <div class="snow-plow__step snow-plow__step--right"></div>
        <div class="snow-plow__cabin">
          <div class="snow-plow__window"></div>
          <div class="snow-plow__light"></div>
        </div>
      </div>
      <div class="snow-plow__fender snow-plow__fender--left"></div>
      <div class="snow-plow__fender snow-plow__fender--right"></div>
      <div class="snow-plow__wheel snow-plow__wheel--left"><span></span></div>
      <div class="snow-plow__wheel snow-plow__wheel--right"><span></span></div>
    </div>
    <div class="snow-ground" aria-hidden="true"></div>
    <footer>
      <div class="footer-inner">
        <p>&copy; 2023 - 2025 Erik Schauer, DO1FFE, do1ffe@darc.de</p>
      </div>
    </footer>
    <script>
      (function () {
        const canvas = document.getElementById("snow-canvas");
        if (!canvas || !canvas.getContext) {
          return;
        }
        const ctx = canvas.getContext("2d");
        const flakes = [];
        const columnWidth = 4;
        let width = 0;
        let height = 0;
        let columns = 0;
        let heightField = [];
        let resizeTimer;
        const baseFallSpeed = 1.3;
        const minFlakeRadius = 0.6;
        const maxFlakeRadius = 1.5;
        const snowDepositScale = 0.6;
        let footerHeight = 0;
        const plowElement = document.getElementById("snow-plow");
        const plowState = {
          active: false,
          x: -240,
          width: 220,
          speed: 0,
          timerId: null,
          y: 0,
          rotation: 0,
          bouncePhase: Math.random() * Math.PI * 2,
          tiltPhase: Math.random() * Math.PI * 2,
          trailSegment: null,
          sprayAccumulator: 0,
        };
        const trailSegments = [];
        const sprayParticles = [];

        function updatePlowTransform() {
          if (plowElement) {
            const translateY = plowState.active ? plowState.y : 0;
            const rotation = plowState.active ? plowState.rotation : 0;
            plowElement.style.transform = `translate3d(${plowState.x}px, ${translateY}px, 0) rotate(${rotation}deg)`;
          }
        }

        function schedulePlow() {
          if (!plowElement) {
            return;
          }
          if (plowState.timerId) {
            clearTimeout(plowState.timerId);
          }
          const delay = 60000 + Math.random() * 60000;
          plowState.timerId = setTimeout(() => {
            plowState.timerId = null;
            startPlow();
          }, delay);
        }

        function startPlow() {
          if (!plowElement || plowState.active || !width) {
            if (!plowState.active) {
              schedulePlow();
            }
            return;
          }
          const rect = plowElement.getBoundingClientRect();
          if (rect.width) {
            plowState.width = rect.width;
          }
          plowState.active = true;
          plowState.x = -plowState.width - 20;
          plowState.speed = Math.max(width / 6, 160);
          plowState.y = 0;
          plowState.rotation = 0;
          plowState.bouncePhase = Math.random() * Math.PI * 2;
          plowState.tiltPhase = Math.random() * Math.PI * 2;
          plowState.sprayAccumulator = 0;
          plowElement.classList.add("is-active");
          const segment = {
            start: Math.max(0, plowState.x),
            end: Math.max(0, plowState.x),
            opacity: 0,
            active: true,
            bankHeight: 22,
          };
          plowState.trailSegment = segment;
          trailSegments.push(segment);
          updatePlowTransform();
        }

        function finishPlow() {
          if (!plowElement) {
            return;
          }
          plowState.active = false;
          plowElement.classList.remove("is-active");
          plowState.x = -plowState.width - 20;
          plowState.y = 0;
          plowState.rotation = 0;
          if (plowState.trailSegment) {
            plowState.trailSegment.active = false;
            plowState.trailSegment = null;
          }
          updatePlowTransform();
          schedulePlow();
        }

        function clearSnowUnderPlow(xPosition, plowWidth) {
          if (!columns || !heightField.length) {
            return;
          }
          const start = Math.floor(xPosition / columnWidth);
          const end = Math.ceil((xPosition + plowWidth) / columnWidth);
          if (start > columns || end < 0) {
            return;
          }
          const clampedStart = Math.max(0, start);
          const clampedEnd = Math.min(columns - 1, end);
          const center = (clampedStart + clampedEnd) / 2;
          const halfWidth = Math.max(1, (clampedEnd - clampedStart) / 2);
          let removedTotal = 0;
          for (let i = clampedStart; i <= clampedEnd; i++) {
            const distance = Math.abs(i - center);
            const factor = 1 - Math.min(1, distance / halfWidth);
            const removal = Math.max(heightField[i] * (0.6 + 0.35 * factor), maxHeight() * 0.2 * factor);
            heightField[i] = Math.max(0, heightField[i] - removal);
            removedTotal += removal;
          }
          if (removedTotal > 0) {
            const span = Math.max(1, clampedEnd - clampedStart + 1);
            const depositBase = (removedTotal / span) * 0.32;
            const rightIndex = clampedEnd + 1;
            const farRightIndex = clampedEnd + 2;
            const forwardIndex = clampedEnd + 3;
            const leftIndex = clampedStart - 1;
            let overflowSnow = 0;
            const depositAtColumn = (index, amount) => {
              if (amount <= 0) {
                return;
              }
              if (index < 0 || index >= columns) {
                overflowSnow += amount;
                return;
              }
              heightField[index] = Math.min(maxHeight(), heightField[index] + amount);
            };
            depositAtColumn(rightIndex, depositBase * 1.8);
            depositAtColumn(farRightIndex, depositBase * 1.5);
            depositAtColumn(forwardIndex, depositBase * 0.9);
            depositAtColumn(leftIndex, depositBase * 0.1);
            if (overflowSnow > 0) {
              const extraSpray = Math.min(6, Math.round(overflowSnow / Math.max(6, maxHeight())));
              for (let i = 0; i < extraSpray; i++) {
                spawnSprayParticle();
              }
            }
          }
        }

        function updatePlow(delta) {
          if (!plowElement || !plowState.active) {
            return;
          }
          const seconds = delta * (1 / 60);
          plowState.x += plowState.speed * seconds;
          plowState.bouncePhase += seconds * 3.6;
          plowState.tiltPhase += seconds * 1.2;
          const midPoint = plowState.x + plowState.width / 2;
          const travelRatio = Math.min(1, Math.max(0, midPoint / Math.max(width, 1)));
          const bounce = Math.sin(plowState.bouncePhase * Math.PI * 2) * 3.2;
          const rumble = Math.sin(plowState.bouncePhase * Math.PI * 4) * 1.2;
          const slopeLift = Math.sin(travelRatio * Math.PI) * 1.6;
          plowState.y = bounce + rumble + slopeLift;
          let steer = Math.sin((plowState.x / Math.max(width, 1)) * Math.PI) * 2.6;
          const wobble = Math.sin(plowState.tiltPhase * Math.PI * 2) * 1.3;
          if (plowState.x < -plowState.width * 0.6) {
            plowState.y *= 0.3;
            steer *= 0.3;
          }
          plowState.rotation = steer + wobble;
          clearSnowUnderPlow(plowState.x + 20, plowState.width - 40);
          const spawnRate = 42;
          plowState.sprayAccumulator += seconds * spawnRate;
          while (plowState.sprayAccumulator >= 1) {
            spawnSprayParticle();
            plowState.sprayAccumulator -= 1;
          }
          updatePlowTransform();
          if (plowState.x > width + plowState.width) {
            finishPlow();
          }
        }

        function spawnSprayParticle() {
          if (!plowState.active) {
            return;
          }
          const nose = plowState.x + plowState.width - 24;
          if (nose < -120 || nose > width + 160) {
            return;
          }
          const columnIndex = Math.min(columns - 1, Math.max(0, Math.floor(nose / columnWidth)));
          const ground = height - (heightField[columnIndex] || 0);
          const direction = Math.random() < 0.82 ? 1 : -0.45;
          const particle = {
            x: nose + Math.random() * 12,
            y: ground - 12 - Math.random() * 10,
            vx: (80 + Math.random() * 90) * direction,
            vy: -140 - Math.random() * 120,
            life: 0,
            ttl: 1.6 + Math.random() * 0.6,
            size: 1.4 + Math.random() * 1.6,
          };
          sprayParticles.push(particle);
        }

        function updateSprayParticles(seconds) {
          const gravity = 420;
          for (let i = sprayParticles.length - 1; i >= 0; i--) {
            const particle = sprayParticles[i];
            particle.vx *= 0.985;
            particle.vy += gravity * seconds;
            particle.x += particle.vx * seconds;
            particle.y += particle.vy * seconds;
            particle.life += seconds;
            const columnIndex = Math.min(columns - 1, Math.max(0, Math.floor(particle.x / columnWidth)));
            const ground = height - (heightField[columnIndex] || 0);
            if (particle.y >= ground) {
              particle.y = ground - Math.random() * 2;
              particle.vy *= -0.32;
              particle.vx *= 0.7;
            }
            if (particle.life > particle.ttl || particle.y > height + 60 || particle.x < -180 || particle.x > width + 220) {
              sprayParticles.splice(i, 1);
            }
          }
        }

        function updateTrailSegments(seconds) {
          const activeSegment = plowState.trailSegment;
          if (activeSegment && plowState.active) {
            const tail = Math.max(0, plowState.x + 24);
            const nose = Math.min(width + plowState.width, plowState.x + plowState.width - 18);
            activeSegment.start = Math.min(activeSegment.start, tail);
            activeSegment.end = Math.max(activeSegment.end, nose);
            const leftColumn = Math.max(0, Math.floor(activeSegment.start / columnWidth));
            const rightColumn = Math.min(columns - 1, Math.floor(activeSegment.end / columnWidth));
            const leftHeight = heightField[leftColumn] || 0;
            const rightHeight = heightField[rightColumn] || 0;
            activeSegment.bankHeight = 8 + Math.min(16, (leftHeight + rightHeight) * 0.12);
            activeSegment.opacity = Math.min(0.45, activeSegment.opacity + seconds * 1.2);
          }
          for (let i = trailSegments.length - 1; i >= 0; i--) {
            const segment = trailSegments[i];
            if (segment.active) {
              continue;
            }
            segment.opacity = Math.max(0, segment.opacity - seconds / 70);
            segment.bankHeight = Math.max(0, (segment.bankHeight || 0) - seconds * 12);
            if (segment.opacity <= 0.02) {
              trailSegments.splice(i, 1);
            }
          }
        }

        function updateFooterHeight() {
          footerHeight = document.querySelector("footer")?.offsetHeight || 0;
          document.documentElement.style.setProperty("--footer-height", `${footerHeight}px`);
          return footerHeight;
        }

        function maxHeight() {
          return height - 6;
        }

        function spawnFlake(offset = 0) {
          return {
            x: Math.random() * width,
            y: -Math.random() * height - offset,
            radius: minFlakeRadius + Math.random() * (maxFlakeRadius - minFlakeRadius),
            speed: 0.35 + Math.random() * 1.1,
            drift: (Math.random() - 0.5) * 0.25,
            phase: Math.random() * Math.PI * 2,
          };
        }

        function resetFlake(flake, offset = 0) {
          const fresh = spawnFlake(offset);
          flake.x = fresh.x;
          flake.y = fresh.y;
          flake.radius = fresh.radius;
          flake.speed = fresh.speed;
          flake.drift = fresh.drift;
          flake.phase = fresh.phase;
        }

        function ensureFlakeCount() {
          const target = Math.max(80, Math.floor(width / 10));
          while (flakes.length < target) {
            flakes.push(spawnFlake(flakes.length * 4));
          }
          while (flakes.length > target) {
            flakes.pop();
          }
        }

        function resizeCanvas() {
          const previousField = heightField.slice();
          const previousColumns = columns || 1;
          const previousWidth = Math.max(width || canvas.clientWidth || 0, 1);
          const doc = document.documentElement;
          width = Math.max(window.innerWidth || 0, doc ? doc.clientWidth : 0, canvas.clientWidth || 0);
          let viewportHeight = Math.max(
            window.innerHeight || 0,
            doc ? doc.clientHeight : 0,
            canvas.clientHeight || 0
          );
          if (!viewportHeight) {
            viewportHeight = 600;
          }
          const effectiveFooterHeight = Math.max(0, footerHeight || 0);
          height = Math.max(viewportHeight - effectiveFooterHeight, 0);
          const devicePixelRatio = window.devicePixelRatio || 1;
          canvas.style.width = "100%";
          canvas.style.height = height + "px";
          canvas.width = Math.floor(width * devicePixelRatio);
          canvas.height = Math.floor(height * devicePixelRatio);
          ctx.setTransform(1, 0, 0, 1, 0, 0);
          ctx.scale(devicePixelRatio, devicePixelRatio);

          columns = Math.max(1, Math.ceil(width / columnWidth));
          heightField = new Array(columns).fill(0);
          if (previousField.length) {
            const scale = previousColumns / columns;
            for (let i = 0; i < columns; i++) {
              const mappedIndex = Math.floor(i * scale);
              heightField[i] = previousField[mappedIndex] || 0;
            }
          }
          ensureFlakeCount();
          const widthRatio = Math.max(width / previousWidth, 0);
          if (trailSegments.length && widthRatio && widthRatio !== 1) {
            trailSegments.forEach((segment) => {
              segment.start *= widthRatio;
              segment.end *= widthRatio;
            });
          }
          if (plowState.active) {
            plowState.x *= widthRatio;
            plowState.speed = Math.max(width / 6, 160);
          } else if (plowElement) {
            const rect = plowElement.getBoundingClientRect();
            if (rect.width) {
              plowState.width = rect.width;
            }
            plowState.x = -plowState.width - 20;
            updatePlowTransform();
          }
          flakes.forEach((flake, index) => resetFlake(flake, index * 2));
        }

        function depositSnow(column, amount) {
          const spread = 3;
          for (let offset = -spread; offset <= spread; offset++) {
            const idx = column + offset;
            if (idx < 0 || idx >= columns) {
              continue;
            }
            const falloff = 1 - Math.abs(offset) / (spread + 1);
            heightField[idx] = Math.min(maxHeight(), heightField[idx] + amount * falloff);
          }
        }

        function relaxHeightField() {
          if (columns < 3) {
            return;
          }
          const snapshot = heightField.slice();
          for (let i = 1; i < columns - 1; i++) {
            const average = (snapshot[i - 1] + snapshot[i] + snapshot[i + 1]) / 3;
            heightField[i] = Math.min(maxHeight(), snapshot[i] * 0.8 + average * 0.2);
          }
        }

        function updateFlakes(multiplier) {
          if (!width || !height) {
            return;
          }
          for (const flake of flakes) {
            flake.phase += 0.015 * multiplier;
            flake.drift += (Math.random() - 0.5) * 0.002 * multiplier;
            flake.x += (Math.sin(flake.phase) * 0.6 + flake.drift) * multiplier;
            flake.y += (flake.speed + baseFallSpeed) * multiplier;

            if (flake.x < 0) {
              flake.x += width;
            } else if (flake.x >= width) {
              flake.x -= width;
            }

            const column = Math.floor(flake.x / columnWidth);
            if (column >= 0 && column < columns) {
              const ground = height - heightField[column];
              if (flake.y + flake.radius >= ground) {
                depositSnow(column, flake.radius * snowDepositScale);
                resetFlake(flake);
                continue;
              }
            }

            if (flake.y - flake.radius > height) {
              resetFlake(flake);
            }
          }
        }

        function drawScene() {
          ctx.clearRect(0, 0, width, height);

          const backgroundGradient = ctx.createLinearGradient(0, height - 120, 0, height);
          backgroundGradient.addColorStop(0, "rgba(255, 255, 255, 0.06)");
          backgroundGradient.addColorStop(1, "rgba(255, 255, 255, 0.18)");
          ctx.fillStyle = backgroundGradient;
          ctx.fillRect(0, height - 120, width, 120);

          if (columns > 0) {
            const snowPoints = [];
            const firstHeight = height - (heightField[0] || 0);
            ctx.beginPath();
            ctx.moveTo(0, height);
            ctx.lineTo(0, firstHeight);
            snowPoints.push({ x: 0, y: firstHeight });
            for (let i = 1; i < columns; i++) {
              const x = i * columnWidth;
              const y = height - heightField[i];
              ctx.lineTo(x, y);
              snowPoints.push({ x, y });
            }
            const lastHeight = height - (heightField[columns - 1] || 0);
            ctx.lineTo(width, lastHeight);
            snowPoints.push({ x: width, y: lastHeight });
            ctx.lineTo(width, height);
            ctx.closePath();

            const snowGradient = ctx.createLinearGradient(0, height - 100, 0, height);
            snowGradient.addColorStop(0, "rgba(255, 255, 255, 0.82)");
            snowGradient.addColorStop(1, "rgba(255, 255, 255, 0.96)");
            ctx.fillStyle = snowGradient;
            ctx.fill();

            ctx.save();
            ctx.beginPath();
            ctx.moveTo(0, firstHeight);
            for (let i = 1; i < snowPoints.length; i++) {
              const point = snowPoints[i];
              ctx.lineTo(point.x, point.y);
            }
            ctx.lineTo(width, height);
            ctx.lineTo(0, height);
            ctx.closePath();
            ctx.clip();

            drawTrailSegments();
            drawSnowShading(snowPoints);

            ctx.restore();

            ctx.beginPath();
            ctx.moveTo(0, firstHeight);
            for (let i = 1; i < snowPoints.length; i++) {
              const point = snowPoints[i];
              ctx.lineTo(point.x, point.y);
            }
            ctx.strokeStyle = "rgba(255, 255, 255, 0.7)";
            ctx.lineWidth = 1.4;
            ctx.stroke();
          }

          drawSprayParticles();

          ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
          for (const flake of flakes) {
            ctx.beginPath();
            ctx.arc(flake.x, flake.y, flake.radius, 0, Math.PI * 2);
            ctx.fill();
          }
        }

        function drawTrailSegments() {
          if (!trailSegments.length) {
            return;
          }
          ctx.save();
          for (const segment of trailSegments) {
            if (!segment) {
              continue;
            }
            const opacity = Math.max(0, Math.min(1, segment.opacity ?? 0));
            if (opacity <= 0.01) {
              continue;
            }
            const start = Math.max(0, segment.start);
            const end = Math.min(width, segment.end);
            if (end <= start) {
              continue;
            }
            const bankHeight = Math.max(12, segment.bankHeight || 22);
            const gradient = ctx.createLinearGradient(0, height - 64, 0, height);
            gradient.addColorStop(0, `rgba(180, 210, 235, ${0.12 * opacity})`);
            gradient.addColorStop(0.5, `rgba(210, 226, 244, ${0.26 * opacity})`);
            gradient.addColorStop(1, `rgba(160, 186, 206, ${0.36 * opacity})`);
            ctx.fillStyle = gradient;
            ctx.beginPath();
            ctx.moveTo(start, height);
            ctx.lineTo(start, height - 10);
            ctx.bezierCurveTo(start + 16, height - bankHeight, end - 16, height - bankHeight, end, height - 12);
            ctx.lineTo(end, height);
            ctx.closePath();
            ctx.fill();

            ctx.beginPath();
            ctx.moveTo(start + 6, height - bankHeight * 0.6);
            ctx.lineTo(end - 6, height - bankHeight * 0.62);
            ctx.strokeStyle = `rgba(255, 255, 255, ${0.18 * opacity})`;
            ctx.lineWidth = 3;
            ctx.stroke();

            ctx.beginPath();
            ctx.moveTo(start - 10, height);
            ctx.lineTo(start + 6, height);
            ctx.lineTo(start + 14, height - bankHeight);
            ctx.lineTo(start - 12, height - bankHeight * 0.45);
            ctx.closePath();
            ctx.fillStyle = `rgba(235, 245, 255, ${0.5 * opacity})`;
            ctx.fill();

            ctx.beginPath();
            ctx.moveTo(end + 10, height);
            ctx.lineTo(end - 6, height);
            ctx.lineTo(end - 16, height - bankHeight * 0.85);
            ctx.lineTo(end + 12, height - bankHeight * 0.55);
            ctx.closePath();
            ctx.fillStyle = `rgba(150, 180, 210, ${0.36 * opacity})`;
            ctx.fill();
          }
          ctx.restore();
        }

        function drawSnowShading(points) {
          if (!points.length) {
            return;
          }
          ctx.save();
          ctx.globalAlpha = 0.3;
          ctx.beginPath();
          ctx.moveTo(points[0].x, points[0].y - 10);
          for (let i = 1; i < points.length; i++) {
            const point = points[i];
            ctx.lineTo(point.x, point.y - 10 - Math.sin(i * 0.35) * 2);
          }
          ctx.lineTo(width, points[points.length - 1].y - 4);
          ctx.lineTo(width, points[points.length - 1].y);
          for (let i = points.length - 1; i >= 0; i--) {
            const point = points[i];
            ctx.lineTo(point.x, point.y + 2);
          }
          ctx.closePath();
          ctx.fillStyle = "rgba(255, 255, 255, 0.32)";
          ctx.fill();
          ctx.restore();

          ctx.save();
          ctx.globalAlpha = 0.22;
          ctx.beginPath();
          ctx.moveTo(points[0].x, points[0].y + 6);
          for (let i = 1; i < points.length; i++) {
            const point = points[i];
            ctx.lineTo(point.x, point.y + 12 + Math.sin(i * 0.4) * 4);
          }
          ctx.lineTo(width, points[points.length - 1].y + 18);
          ctx.lineTo(width, height);
          ctx.lineTo(0, height);
          ctx.closePath();
          ctx.fillStyle = "rgba(60, 92, 130, 0.32)";
          ctx.fill();
          ctx.restore();
        }

        function drawSprayParticles() {
          if (!sprayParticles.length) {
            return;
          }
          for (const particle of sprayParticles) {
            const fade = 1 - Math.min(1, particle.life / particle.ttl);
            if (fade <= 0) {
              continue;
            }
            const angle = Math.atan2(particle.vy, particle.vx) + Math.PI / 2;
            const stretchX = 1 + Math.min(1.5, Math.abs(particle.vx) / 160);
            const stretchY = 0.7 + Math.min(0.6, Math.abs(particle.vy) / 280);
            ctx.save();
            ctx.translate(particle.x, particle.y);
            ctx.rotate(angle);
            ctx.scale(stretchX, stretchY);
            ctx.beginPath();
            ctx.arc(0, 0, particle.size, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(255, 255, 255, ${0.9 * fade})`;
            ctx.fill();
            ctx.restore();

            ctx.save();
            ctx.globalAlpha = fade * 0.4;
            ctx.beginPath();
            ctx.arc(particle.x, particle.y, particle.size * 1.1, 0, Math.PI * 2);
            ctx.strokeStyle = "rgba(200, 220, 240, 0.9)";
            ctx.lineWidth = 0.6;
            ctx.stroke();
            ctx.restore();
          }
        }

        function handleResize() {
          updateFooterHeight();
          resizeCanvas();
        }

        handleResize();
        if (plowElement) {
          const rect = plowElement.getBoundingClientRect();
          if (rect.width) {
            plowState.width = rect.width;
          }
          plowState.x = -plowState.width - 20;
          updatePlowTransform();
          schedulePlow();
        }
        window.addEventListener("load", handleResize);
        window.addEventListener("resize", () => {
          updateFooterHeight();
          clearTimeout(resizeTimer);
          resizeTimer = setTimeout(handleResize, 150);
        });

        let lastTime = performance.now();
        function frame(now) {
          const delta = Math.min((now - lastTime) / 16.67, 3);
          lastTime = now;
          const seconds = delta * (1 / 60);
          updateFlakes(delta);
          relaxHeightField();
          updatePlow(delta);
          updateTrailSegments(seconds);
          updateSprayParticles(seconds);
          drawScene();
          requestAnimationFrame(frame);
        }

        requestAnimationFrame(frame);
      })();
    </script>
  </body>
</html>
'''

LOGIN_PAGE = '''
<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Anmeldung – Adventskalender</title>
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Mountains+of+Christmas:wght@400;700&family=Open+Sans:wght@400;600&display=swap');
      * { box-sizing: border-box; }
      body {
        font-family: 'Open Sans', Arial, sans-serif;
        margin: 0;
        min-height: 100vh;
        color: #f8f9fa;
        background: linear-gradient(180deg, #0b1d2b 0%, #12324a 50%, #1c5560 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 30px 15px;
      }
      .auth-card {
        width: min(100%, 420px);
        background: rgba(12, 35, 52, 0.85);
        border-radius: 18px;
        padding: 32px 28px;
        box-shadow: 0 18px 40px rgba(0, 0, 0, 0.45);
        border: 1px solid rgba(255, 255, 255, 0.2);
      }
      h1 {
        font-family: 'Mountains of Christmas', 'Open Sans', cursive;
        text-align: center;
        margin: 0 0 18px;
        font-size: 2.2rem;
      }
      form {
        display: grid;
        gap: 16px;
      }
      label {
        font-weight: 600;
      }
      input[type="email"],
      input[type="password"],
      input[type="text"] {
        padding: 12px 14px;
        border-radius: 10px;
        border: none;
        background: rgba(255, 255, 255, 0.9);
        color: #12324a;
        font-size: 1rem;
      }
      button {
        padding: 12px 18px;
        border-radius: 10px;
        border: none;
        background: linear-gradient(135deg, #ff7b7b, #ffcf5c);
        color: #1b1b1b;
        font-weight: 700;
        cursor: pointer;
        text-transform: uppercase;
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.35);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }
      button:hover {
        transform: translateY(-2px);
        box-shadow: 0 10px 20px rgba(0, 0, 0, 0.4);
      }
      .error {
        background: rgba(220, 53, 69, 0.75);
        border-radius: 10px;
        padding: 12px 16px;
        font-weight: 600;
        text-align: center;
      }
      .message {
        background: rgba(25, 135, 84, 0.75);
        border-radius: 10px;
        padding: 12px 16px;
        font-weight: 600;
        text-align: center;
      }
      .switch {
        margin-top: 18px;
        text-align: center;
      }
      a {
        color: #ffcf5c;
        font-weight: 600;
        text-decoration: none;
      }
      a:hover {
        text-decoration: underline;
      }
    </style>
  </head>
  <body>
    <div class="auth-card">
      <h1>Anmeldung</h1>
      {% if message %}
        <div class="message">{{ message }}</div>
      {% endif %}
      {% if error %}
        <div class="error">{{ error }}</div>
      {% endif %}
      <form method="post" novalidate>
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div>
          <label for="email">E-Mail-Adresse</label>
          <input type="email" id="email" name="email" value="{{ email or '' }}" required autocomplete="email">
        </div>
        <div>
          <label for="password">Passwort</label>
          <input type="password" id="password" name="password" required autocomplete="current-password">
        </div>
        <button type="submit">Login</button>
      </form>
      <div class="switch">
        Noch kein Konto? <a href="/register">Jetzt registrieren</a>
      </div>
    </div>
  </body>
</html>
'''

REGISTER_PAGE = '''
<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Registrierung – Adventskalender</title>
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Mountains+of+Christmas:wght@400;700&family=Open+Sans:wght@400;600&display=swap');
      * { box-sizing: border-box; }
      body {
        font-family: 'Open Sans', Arial, sans-serif;
        margin: 0;
        min-height: 100vh;
        color: #f8f9fa;
        background: linear-gradient(180deg, #0b1d2b 0%, #12324a 50%, #1c5560 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 30px 15px;
      }
      .auth-card {
        width: min(100%, 460px);
        background: rgba(12, 35, 52, 0.85);
        border-radius: 18px;
        padding: 32px 28px;
        box-shadow: 0 18px 40px rgba(0, 0, 0, 0.45);
        border: 1px solid rgba(255, 255, 255, 0.2);
      }
      h1 {
        font-family: 'Mountains of Christmas', 'Open Sans', cursive;
        text-align: center;
        margin: 0 0 18px;
        font-size: 2.2rem;
      }
      form {
        display: grid;
        gap: 16px;
      }
      label {
        font-weight: 600;
      }
      input[type="email"],
      input[type="password"],
      input[type="text"] {
        padding: 12px 14px;
        border-radius: 10px;
        border: none;
        background: rgba(255, 255, 255, 0.9);
        color: #12324a;
        font-size: 1rem;
      }
      button {
        padding: 12px 18px;
        border-radius: 10px;
        border: none;
        background: linear-gradient(135deg, #34d399, #60a5fa);
        color: #0b1d2b;
        font-weight: 700;
        cursor: pointer;
        text-transform: uppercase;
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.35);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }
      button:hover {
        transform: translateY(-2px);
        box-shadow: 0 10px 20px rgba(0, 0, 0, 0.4);
      }
      .error {
        background: rgba(220, 53, 69, 0.75);
        border-radius: 10px;
        padding: 12px 16px;
        font-weight: 600;
        text-align: center;
      }
      .switch {
        margin-top: 18px;
        text-align: center;
      }
      a {
        color: #ffcf5c;
        font-weight: 600;
        text-decoration: none;
      }
      a:hover {
        text-decoration: underline;
      }
    </style>
  </head>
  <body>
    <div class="auth-card">
      <h1>Registrieren</h1>
      {% if error %}
        <div class="error">{{ error }}</div>
      {% endif %}
      <form method="post" novalidate>
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div>
          <label for="display_name">Anzeigename</label>
          <input type="text" id="display_name" name="display_name" value="{{ display_name or '' }}" placeholder="Rufzeichen oder Name" required>
        </div>
        <div>
          <label for="email">E-Mail-Adresse</label>
          <input type="email" id="email" name="email" value="{{ email or '' }}" required autocomplete="email">
        </div>
        <div>
          <label for="password">Passwort</label>
          <input type="password" id="password" name="password" required autocomplete="new-password">
        </div>
        <div>
          <label for="confirm_password">Passwort bestätigen</label>
          <input type="password" id="confirm_password" name="confirm_password" required autocomplete="new-password">
        </div>
        <button type="submit">Konto anlegen</button>
      </form>
      <div class="switch">
        Bereits registriert? <a href="/login">Zur Anmeldung</a>
      </div>
    </div>
  </body>
</html>
'''

GENERIC_PAGE = '''
<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Adventskalender</title>
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Mountains+of+Christmas:wght@400;700&family=Open+Sans:wght@400;600&display=swap');
      * { box-sizing: border-box; }
      body {
        font-family: 'Open Sans', Arial, sans-serif;
        margin: 0;
        min-height: 100vh;
        color: #f8f9fa;
        background: linear-gradient(180deg, #0b1d2b 0%, #12324a 50%, #1c5560 100%);
        position: relative;
        overflow-x: hidden;
        padding-bottom: calc(var(--footer-height, 160px) + 40px);
      }
      body::before,
      body::after {
        content: "";
        position: fixed;
        top: -240px;
        left: -12%;
        width: 124%;
        height: calc(100% + 380px);
        pointer-events: none;
        will-change: background-position;
        background-repeat: repeat;
        z-index: 0;
      }
      body::before {
        background-image: radial-gradient(2.2px 2.2px at 30px 30px, rgba(255,255,255,0.95) 55%, transparent 58%),
                          radial-gradient(2.6px 2.6px at 120px 80px, rgba(255,255,255,0.75) 55%, transparent 58%),
                          radial-gradient(1.6px 1.6px at 200px 150px, rgba(255,255,255,0.9) 55%, transparent 58%),
                          radial-gradient(2.4px 2.4px at 80px 200px, rgba(255,255,255,0.82) 55%, transparent 58%),
                          radial-gradient(1.8px 1.8px at 160px 40px, rgba(255,255,255,0.88) 55%, transparent 58%);
        background-size: 220px 220px, 260px 260px, 200px 200px, 240px 240px, 210px 210px;
        background-position: 0 0, 60px 100px, 140px 40px, 40px 160px, 90px 10px;
        animation: snowFallNear 28s linear infinite;
        opacity: 0.6;
        filter: blur(0.3px);
      }
      body::after {
        background-image: radial-gradient(1.6px 1.6px at 40px 40px, rgba(255,255,255,0.85) 55%, transparent 58%),
                          radial-gradient(2.4px 2.4px at 90px 120px, rgba(255,255,255,0.6) 55%, transparent 58%),
                          radial-gradient(1.2px 1.2px at 150px 60px, rgba(255,255,255,0.8) 55%, transparent 58%),
                          radial-gradient(2px 2px at 200px 180px, rgba(255,255,255,0.65) 55%, transparent 58%),
                          radial-gradient(1.4px 1.4px at 20px 170px, rgba(255,255,255,0.9) 55%, transparent 58%);
        background-size: 240px 240px, 220px 220px, 200px 200px, 260px 260px, 210px 210px;
        background-position: 30px 50px, 110px 10px, 160px 140px, 80px 200px, 0 120px;
        animation: snowFallFar 38s linear infinite;
        opacity: 0.45;
        filter: blur(0.8px);
      }
      @keyframes snowFallNear {
        0% { background-position: 0 0, 60px 100px, 140px 40px, 40px 160px, 90px 10px; }
        100% { background-position: -40px 220px, 20px 360px, 100px 240px, 0 400px, 50px 220px; }
      }
      @keyframes snowFallFar {
        0% { background-position: 30px 50px, 110px 10px, 160px 140px, 80px 200px, 0 120px; }
        100% { background-position: -10px 290px, 150px 230px, 120px 340px, 40px 460px, 40px 330px; }
      }
      @keyframes snowDrift {
        from { transform: translate3d(0, 0, 0); }
        to { transform: translate3d(-180px, 0, 0); }
      }
      #snow-canvas {
        position: fixed;
        left: 0;
        bottom: 0;
        width: 100%;
        height: 180px;
        pointer-events: none;
        z-index: 2;
      }
      .snow-ground {
        position: fixed;
        left: 0;
        bottom: 0;
        width: 100%;
        height: 160px;
        pointer-events: none;
        background: linear-gradient(180deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.22) 30%, rgba(255,255,255,0.85) 100%);
        box-shadow: 0 -28px 48px rgba(255, 255, 255, 0.18);
        overflow: hidden;
        z-index: 1;
      }
      .snow-ground::before,
      .snow-ground::after {
        content: none;
      }
      header, footer {
        padding: 18px;
        background: #0f2e48;
        text-align: center;
        border-bottom: 2px solid rgba(255, 255, 255, 0.2);
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.4);
      }
      footer {
        border-bottom: none;
        border-top: 2px solid rgba(255, 255, 255, 0.2);
        position: fixed;
        bottom: 0;
        left: 0;
        width: 100%;
        display: flex;
        justify-content: center;
        z-index: 1000;
        align-items: center;
      }
      .footer-inner {
        width: min(100%, 960px);
        margin: 0 auto;
        text-align: center;
        padding: 0 24px;
      }
      nav a {
        margin: 0 10px;
        color: #ffeecf;
        text-decoration: none;
        font-weight: 600;
        transition: color 0.3s ease;
      }
      nav a:hover {
        color: #ffcf5c;
      }
      .content {
        position: relative;
        z-index: 3;
        max-width: 720px;
        margin: 50px auto;
        background: rgba(12, 35, 52, 0.8);
        padding: 30px;
        border-radius: 16px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
        text-align: center;
        line-height: 1.6;
      }
      footer p {
        margin: 0;
        color: #ffeecf;
      }
      a.button-link {
        display: inline-block;
        margin-top: 20px;
        padding: 10px 18px;
        border-radius: 8px;
        background: linear-gradient(135deg, #ff7b7b, #ffcf5c);
        color: #1b1b1b;
        font-weight: 700;
        text-decoration: none;
        text-transform: uppercase;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }
      a.button-link:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 15px rgba(0, 0, 0, 0.35);
      }
    </style>
  </head>
  <body>
    <header>
      <nav>
        <a href="/">Zurück zum Adventskalender</a>
      </nav>
    </header>
    <main class="content">{{ content }}</main>
    <canvas id="snow-canvas" aria-hidden="true"></canvas>
    <div class="snow-ground" aria-hidden="true"></div>
    <footer>
      <div class="footer-inner">
        <p>&copy; 2023 - 2025 Erik Schauer, DO1FFE, do1ffe@darc.de</p>
      </div>
    </footer>
    <script>
      (function () {
        const canvas = document.getElementById("snow-canvas");
        if (!canvas || !canvas.getContext) {
          return;
        }
        const ctx = canvas.getContext("2d");
        const flakes = [];
        const columnWidth = 4;
        let width = 0;
        let height = 160;
        let columns = 0;
        let heightField = [];
        let resizeTimer;
        const baseFallSpeed = 1.3;
        const minFlakeRadius = 0.6;
        const maxFlakeRadius = 1.5;
        const snowDepositScale = 0.6;

        function maxHeight() {
          return height - 6;
        }

        function spawnFlake(offset = 0) {
          return {
            x: Math.random() * width,
            y: -Math.random() * height - offset,
            radius: minFlakeRadius + Math.random() * (maxFlakeRadius - minFlakeRadius),
            speed: 0.35 + Math.random() * 1.1,
            drift: (Math.random() - 0.5) * 0.25,
            phase: Math.random() * Math.PI * 2,
          };
        }

        function resetFlake(flake, offset = 0) {
          const fresh = spawnFlake(offset);
          flake.x = fresh.x;
          flake.y = fresh.y;
          flake.radius = fresh.radius;
          flake.speed = fresh.speed;
          flake.drift = fresh.drift;
          flake.phase = fresh.phase;
        }

        function ensureFlakeCount() {
          const target = Math.max(80, Math.floor(width / 10));
          while (flakes.length < target) {
            flakes.push(spawnFlake(flakes.length * 4));
          }
          while (flakes.length > target) {
            flakes.pop();
          }
        }

        function resizeCanvas() {
          const previousField = heightField.slice();
          const previousColumns = columns || 1;
          width = window.innerWidth;
          height = Math.min(220, Math.max(140, Math.round(window.innerHeight * 0.22)));
          const devicePixelRatio = window.devicePixelRatio || 1;
          canvas.style.width = "100%";
          canvas.style.height = height + "px";
          canvas.width = Math.floor(width * devicePixelRatio);
          canvas.height = Math.floor(height * devicePixelRatio);
          ctx.setTransform(1, 0, 0, 1, 0, 0);
          ctx.scale(devicePixelRatio, devicePixelRatio);

          columns = Math.max(1, Math.ceil(width / columnWidth));
          heightField = new Array(columns).fill(0);
          if (previousField.length) {
            const scale = previousColumns / columns;
            for (let i = 0; i < columns; i++) {
              const mappedIndex = Math.floor(i * scale);
              heightField[i] = previousField[mappedIndex] || 0;
            }
          }
          ensureFlakeCount();
          flakes.forEach((flake, index) => resetFlake(flake, index * 2));
        }

        function depositSnow(column, amount) {
          const spread = 3;
          for (let offset = -spread; offset <= spread; offset++) {
            const idx = column + offset;
            if (idx < 0 || idx >= columns) {
              continue;
            }
            const falloff = 1 - Math.abs(offset) / (spread + 1);
            heightField[idx] = Math.min(maxHeight(), heightField[idx] + amount * falloff);
          }
        }

        function relaxHeightField() {
          if (columns < 3) {
            return;
          }
          const snapshot = heightField.slice();
          for (let i = 1; i < columns - 1; i++) {
            const average = (snapshot[i - 1] + snapshot[i] + snapshot[i + 1]) / 3;
            heightField[i] = Math.min(maxHeight(), snapshot[i] * 0.8 + average * 0.2);
          }
        }

        function updateFlakes(multiplier) {
          if (!width || !height) {
            return;
          }
          for (const flake of flakes) {
            flake.phase += 0.015 * multiplier;
            flake.drift += (Math.random() - 0.5) * 0.002 * multiplier;
            flake.x += (Math.sin(flake.phase) * 0.6 + flake.drift) * multiplier;
            flake.y += (flake.speed + baseFallSpeed) * multiplier;

            if (flake.x < 0) {
              flake.x += width;
            } else if (flake.x >= width) {
              flake.x -= width;
            }

            const column = Math.floor(flake.x / columnWidth);
            if (column >= 0 && column < columns) {
              const ground = height - heightField[column];
              if (flake.y + flake.radius >= ground) {
                depositSnow(column, flake.radius * snowDepositScale);
                resetFlake(flake);
                continue;
              }
            }

            if (flake.y - flake.radius > height) {
              resetFlake(flake);
            }
          }
        }

        function drawScene() {
          ctx.clearRect(0, 0, width, height);

          const backgroundGradient = ctx.createLinearGradient(0, height - 120, 0, height);
          backgroundGradient.addColorStop(0, "rgba(255, 255, 255, 0.06)");
          backgroundGradient.addColorStop(1, "rgba(255, 255, 255, 0.18)");
          ctx.fillStyle = backgroundGradient;
          ctx.fillRect(0, height - 120, width, 120);

          if (columns > 0) {
            const firstHeight = height - (heightField[0] || 0);
            ctx.beginPath();
            ctx.moveTo(0, height);
            ctx.lineTo(0, firstHeight);
            for (let i = 1; i < columns; i++) {
              const x = i * columnWidth;
              const y = height - heightField[i];
              ctx.lineTo(x, y);
            }
            const lastHeight = height - (heightField[columns - 1] || 0);
            ctx.lineTo(width, lastHeight);
            ctx.lineTo(width, height);
            ctx.closePath();

            const snowGradient = ctx.createLinearGradient(0, height - 100, 0, height);
            snowGradient.addColorStop(0, "rgba(255, 255, 255, 0.82)");
            snowGradient.addColorStop(1, "rgba(255, 255, 255, 0.96)");
            ctx.fillStyle = snowGradient;
            ctx.fill();

            ctx.beginPath();
            ctx.moveTo(0, firstHeight);
            for (let i = 1; i < columns; i++) {
              const x = i * columnWidth;
              const y = height - heightField[i];
              ctx.lineTo(x, y);
            }
            ctx.strokeStyle = "rgba(255, 255, 255, 0.7)";
            ctx.lineWidth = 1.4;
            ctx.stroke();
          }

          ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
          for (const flake of flakes) {
            ctx.beginPath();
            ctx.arc(flake.x, flake.y, flake.radius, 0, Math.PI * 2);
            ctx.fill();
          }
        }

        resizeCanvas();
        window.addEventListener("resize", () => {
          clearTimeout(resizeTimer);
          resizeTimer = setTimeout(resizeCanvas, 150);
        });

        let lastTime = performance.now();
        function frame(now) {
          const delta = Math.min((now - lastTime) / 16.67, 3);
          lastTime = now;
          updateFlakes(delta);
          relaxHeightField();
          drawScene();
          requestAnimationFrame(frame);
        }

        requestAnimationFrame(frame);
      })();
    </script>
  </body>
</html>
'''

# Route für die Admin-Seite hinzufügen
def lese_datei(path, fallback):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as file:
            return file.read()
    return fallback


@app.route('/admin', methods=['GET', 'POST'])
def admin_page():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    user = get_user_by_id(user_id)
    if not is_admin_user(user):
        if DEBUG:
            logging.debug("Admin-Zugriff verweigert für Benutzer: %s", user_id)
        return redirect(url_for('startseite'))

    if DEBUG:
        logging.debug("Admin-Seite aufgerufen von %s", user.get("email"))

    message = ""
    is_error = False
    prizes = load_prizes()
    calendar_active = get_calendar_active()

    if request.method == 'POST':
        action = request.form.get('action', 'update_prizes')

        if action == 'update_status':
            new_status = request.form.get('calendar_active') == 'on'
            set_calendar_active(new_status)
            calendar_active = new_status
            message = "Der Kalender wurde aktiviert." if new_status else "Der Kalender wurde deaktiviert."
        elif action == 'update_user':
            target_user_id = request.form.get('user_id')
            email = request.form.get('email', '')
            display_name = request.form.get('display_name', '')
            password = request.form.get('password') or None

            try:
                updated_user = update_user(target_user_id, email, display_name, password=password)
            except ValueError as exc:
                is_error = True
                message = str(exc)
            else:
                message = f"Benutzerdaten für {updated_user.get('email')} wurden aktualisiert."
        elif action == 'delete_user':
            target_user_id = request.form.get('user_id')

            if not target_user_id:
                is_error = True
                message = "Es wurde kein Benutzer zum Löschen ausgewählt."
            elif str(target_user_id) == str(user_id):
                is_error = True
                message = "Der angemeldete Benutzer kann sich nicht selbst löschen."
            else:
                try:
                    deleted_user, rewards = delete_user_and_release_rewards(target_user_id)
                except ValueError as exc:
                    is_error = True
                    message = str(exc)
                else:
                    released_count = sum(1 for reward in rewards if reward.get('prize_name'))
                    if released_count:
                        message = (
                            f"Benutzer {deleted_user.get('email')} wurde gelöscht und "
                            f"{released_count} Gewinn(e) wurden freigegeben."
                        )
                    else:
                        message = f"Benutzer {deleted_user.get('email')} wurde gelöscht."
                    prizes = load_prizes()
        elif action == 'reset_teilnehmer':
            try:
                with open('teilnehmer.txt', 'w', encoding='utf-8'):
                    pass
                message = "Teilnehmerliste wurde geleert."
            except OSError as exc:
                is_error = True
                message = f"Teilnehmerdatei konnte nicht geleert werden: {exc}"

        elif action == 'reset_gewinner':
            try:
                with open('gewinner.txt', 'w', encoding='utf-8'):
                    pass
                message = "Gewinnerliste wurde geleert."
            except OSError as exc:
                is_error = True
                message = f"Gewinnerdatei konnte nicht geleert werden: {exc}"

        elif action == 'reset_qr_codes':
            try:
                if os.path.exists('qr_codes'):
                    for filename in os.listdir('qr_codes'):
                        path = os.path.join('qr_codes', filename)
                        if os.path.isfile(path) or os.path.islink(path):
                            os.remove(path)
                        elif os.path.isdir(path):
                            shutil.rmtree(path)
                message = "QR-Codes wurden gelöscht."
            except OSError as exc:
                is_error = True
                message = f"QR-Codes konnten nicht gelöscht werden: {exc}"

        else:
            raw_prizes = request.form.get('prize_data', '')
            try:
                prizes = parse_prize_configuration(raw_prizes)
                save_prizes(prizes)
                message = "Preise wurden aktualisiert."
            except ValueError as exc:
                is_error = True
                message = str(exc)

        prizes = load_prizes()
        calendar_active = get_calendar_active()

    prizes, total_prizes, remaining_prizes, awarded_prizes = get_prize_stats(prizes)
    prize_lines = format_prize_lines(prizes)
    registered_users = get_all_users()

    qr_files = []
    if os.path.exists('qr_codes'):
        qr_files = sorted(os.listdir('qr_codes'))

    teilnehmer_inhalt = lese_datei('teilnehmer.txt', 'Keine Teilnehmerdaten vorhanden.')
    gewinner_inhalt = lese_datei('gewinner.txt', 'Keine Gewinnerdaten vorhanden.')

    return render_template_string(
        ADMIN_PAGE,
        qr_files=qr_files,
        teilnehmer_inhalt=teilnehmer_inhalt,
        gewinner_inhalt=gewinner_inhalt,
        prizes=prizes,
        prize_lines=prize_lines,
        total_prizes=total_prizes,
        remaining_prizes=remaining_prizes,
        awarded_prizes=awarded_prizes,
        message=message,
        is_error=is_error,
        calendar_active=calendar_active,
        registered_users=registered_users,
    )

# HTML-Template für die Admin-Seite aktualisieren
ADMIN_PAGE = '''
<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Adminbereich - Adventskalender</title>
    <style>
      :root {
        color-scheme: light dark;
      }
      body {
        margin: 0;
        padding: 2rem;
        padding-bottom: calc(var(--footer-height, 160px) + 40px);
        background: #f5f7fa;
        font-family: 'Open Sans', Arial, sans-serif;
        color: #1b2a35;
      }
      main {
        max-width: 1100px;
        margin: 0 auto;
        background: #ffffff;
        padding: 2rem;
        border-radius: 16px;
        box-shadow: 0 12px 35px rgba(15, 23, 42, 0.12);
      }
      nav {
        margin-bottom: 1.5rem;
      }
      nav a {
        color: #0b7285;
        text-decoration: none;
        font-weight: 600;
      }
      nav a:hover {
        text-decoration: underline;
      }
      h1 {
        margin-top: 0;
        font-size: 2rem;
      }
      .panel {
        border: 1px solid #d9e2ec;
        border-radius: 12px;
        padding: 1.5rem;
        background: #f9fbfd;
        margin-bottom: 1.5rem;
      }
      .panel form {
        margin-top: 1rem;
      }
      .status-form {
        display: flex;
        align-items: center;
        gap: 1rem;
        flex-wrap: wrap;
      }
      .status-form label {
        display: flex;
        align-items: center;
        gap: 0.6rem;
        font-weight: 600;
      }
      .status-hint {
        margin-top: 0.75rem;
        color: #334e68;
      }
      .grid {
        display: grid;
        gap: 1.5rem;
      }
      @media (min-width: 900px) {
        .grid {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
      }
      textarea {
        width: 100%;
        min-height: 150px;
        border-radius: 10px;
        border: 1px solid #c3d0e0;
        padding: 0.75rem;
        font-family: 'Fira Code', monospace;
        resize: vertical;
        margin: 0.75rem 0 1rem;
      }
      button {
        background: #0b7285;
        color: #ffffff;
        border: none;
        border-radius: 8px;
        padding: 0.75rem 1.5rem;
        font-size: 1rem;
        font-weight: 600;
        cursor: pointer;
      }
      button:hover {
        background: #095c6a;
      }
      .table-wrapper {
        overflow-x: auto;
      }
      .data-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.95rem;
      }
      .data-table th,
      .data-table td {
        padding: 0.6rem 0.75rem;
        border-bottom: 1px solid #d9e2ec;
        text-align: left;
        white-space: nowrap;
      }
      .data-table th {
        font-weight: 700;
        background: #e3f8ff;
      }
      .data-table tr:nth-child(even) td {
        background: #f5fbff;
      }
      .data-table .col-email {
        min-width: 220px;
      }
      .data-table .col-display-name {
        min-width: 200px;
      }
      .data-table .col-password {
        min-width: 180px;
      }
      .user-table input {
        width: 100%;
        padding: 0.45rem 0.5rem;
        border-radius: 6px;
        border: 1px solid #c3d0e0;
        background: #ffffff;
        box-sizing: border-box;
        font-size: 0.95rem;
      }
      .user-table input::placeholder {
        color: #9aa6b2;
      }
      .user-table td {
        vertical-align: top;
      }
      .user-inline-form {
        margin: 0;
      }
      .user-actions {
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        gap: 0.4rem;
      }
      .user-actions .user-id {
        font-size: 0.85rem;
        color: #486581;
        font-weight: 600;
      }
      .user-actions button {
        width: fit-content;
      }
      .user-actions .danger {
        background: #c92a2a;
      }
      .user-actions .danger:hover {
        background: #a51111;
      }
      @media (min-width: 720px) {
        .user-actions {
          flex-direction: row;
          align-items: center;
        }
        .user-actions .user-id {
          margin-right: 0.75rem;
        }
        .user-actions form + form {
          margin-left: 0.5rem;
        }
      }
      ul {
        padding-left: 1.2rem;
      }
      ul li {
        margin-bottom: 0.35rem;
      }
      pre {
        white-space: pre-wrap;
        font-family: 'Fira Code', monospace;
        background: #ffffff;
        border: 1px solid #d9e2ec;
        border-radius: 10px;
        padding: 1rem;
        max-height: 320px;
        overflow-y: auto;
      }
      .qr-list {
        display: grid;
        gap: 1rem;
        grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      }
      .qr-card {
        text-align: center;
        padding: 1rem;
        border: 1px solid #d9e2ec;
        border-radius: 10px;
        background: #ffffff;
      }
      .qr-card img {
        max-width: 100%;
        height: auto;
      }
      .message {
        border-radius: 10px;
        padding: 0.75rem 1rem;
        margin-bottom: 1.5rem;
        font-weight: 600;
      }
      .message.ok {
        background: #e6f4ea;
        color: #0f5132;
        border: 1px solid #badbcc;
      }
      .message.error {
        background: #fcebea;
        color: #842029;
        border: 1px solid #f5c2c7;
      }
      footer {
        text-align: center;
        color: #ffeecf;
        font-size: 0.9rem;
        background: #0f2e48;
        padding: 1rem;
        border-top: 2px solid rgba(255, 255, 255, 0.2);
        box-shadow: 0 -4px 15px rgba(0, 0, 0, 0.25);
        position: fixed;
        bottom: 0;
        left: 0;
        width: 100%;
        display: flex;
        justify-content: center;
        z-index: 1000;
        align-items: center;
      }
      .footer-inner {
        width: min(100%, 1100px);
        margin: 0 auto;
        text-align: center;
      }
    </style>
  </head>
  <body>
    <main>
      <nav><a href="/">Zurück zum Adventskalender</a></nav>
      <h1>Adminbereich</h1>
      {% if message %}
        <div class="message {{ 'error' if is_error else 'ok' }}">{{ message }}</div>
      {% endif %}

      <section class="panel">
        <h2>Kalenderstatus</h2>
        <form method="post" class="status-form">
          <input type="hidden" name="action" value="update_status">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <label>
            <input type="checkbox" name="calendar_active" {% if calendar_active %}checked{% endif %}>
            Adventskalender ist aktiv
          </label>
          <button type="submit">Status speichern</button>
        </form>
        <p class="status-hint">
          {% if calendar_active %}
            Der Kalender ist aktuell für Besucher freigeschaltet.
          {% else %}
            Der Kalender ist derzeit deaktiviert und für Besucher gesperrt.
          {% endif %}
        </p>
      </section>

      <section class="panel">
        <h2>Preise verwalten</h2>
        <p>Eintrag pro Zeile im Format <code>Name | Sponsor=Gesamt</code> oder <code>Name | Sponsor=Gesamt/Verfügbar</code>. Optional kann ein Link mit <code>Name | Sponsor (https://link)=...</code> angegeben werden. Der Sponsor ist optional; Zeilen mit Anzahl 0 werden ignoriert.</p>
        <form method="post">
          <input type="hidden" name="action" value="update_prizes">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <textarea name="prize_data" id="prize_data">{{ prize_lines }}</textarea>
          <button type="submit">Preise speichern</button>
        </form>
        <p><strong>Gesamtpreise:</strong> {{ total_prizes }} &middot; <strong>Bereits vergeben:</strong> {{ awarded_prizes }} &middot; <strong>Noch verfügbar:</strong> {{ remaining_prizes }}</p>
        <ul>
          {% for prize in prizes %}
            <li>
              <strong>{{ prize.name }}</strong>
              {% if prize.get('sponsor') %}
                <em>(Sponsor:
                  {% if prize.get('sponsor_link') %}
                    <a href="{{ prize.get('sponsor_link') }}" target="_blank" rel="noopener noreferrer">{{ prize.get('sponsor') }}</a>
                  {% else %}
                    {{ prize.get('sponsor') }}
                  {% endif %}
                )</em>
              {% endif %}
              : {{ prize.remaining }} von {{ prize.total }} verfügbar
            </li>
          {% endfor %}
        </ul>
      </section>

      <section class="panel">
        <h2>Registrierte Nutzer ({{ registered_users|length }})</h2>
        {% if registered_users %}
          <div class="table-wrapper">
            <table class="data-table user-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th class="col-email">E-Mail</th>
                  <th class="col-display-name">Anzeigename</th>
                  <th class="col-password">Neues Passwort</th>
                  <th>Aktionen</th>
                </tr>
              </thead>
              <tbody>
                {% for registered_user in registered_users %}
                  {% set form_id = 'user-form-' ~ registered_user.id %}
                  <tr>
                    <td>{{ loop.index }}</td>
                    <td>
                      <input
                        form="{{ form_id }}"
                        type="email"
                        name="email"
                        value="{{ registered_user.email }}"
                        required
                      >
                    </td>
                    <td>
                      <input
                        form="{{ form_id }}"
                        type="text"
                        name="display_name"
                        value="{{ registered_user.display_name }}"
                        required
                      >
                    </td>
                    <td>
                      <input
                        form="{{ form_id }}"
                        type="password"
                        name="password"
                        placeholder="Optional"
                        minlength="8"
                      >
                    </td>
                    <td>
                      <div class="user-actions">
                        <span class="user-id">ID: {{ registered_user.id }}</span>
                        <form id="{{ form_id }}" method="post" class="user-inline-form">
                          <input type="hidden" name="action" value="update_user">
                          <input type="hidden" name="user_id" value="{{ registered_user.id }}">
                          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                          <button type="submit">Speichern</button>
                        </form>
                        <form
                          method="post"
                          class="user-inline-form"
                          onsubmit="return confirm('Soll der Benutzer {{ registered_user.email }} wirklich gelöscht werden? Dies kann nicht rückgängig gemacht werden.');"
                        >
                          <input type="hidden" name="action" value="delete_user">
                          <input type="hidden" name="user_id" value="{{ registered_user.id }}">
                          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                          <button type="submit" class="danger">Löschen</button>
                        </form>
                      </div>
                    </td>
                  </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
        {% else %}
          <p>Noch keine Nutzer registriert.</p>
        {% endif %}
      </section>

      <div class="grid">
        <section class="panel">
          <h2>Teilnehmer</h2>
          <pre>{{ teilnehmer_inhalt }}</pre>
          <form method="post">
            <input type="hidden" name="action" value="reset_teilnehmer">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button type="submit">Teilnehmer zurücksetzen</button>
          </form>
        </section>
        <section class="panel">
          <h2>Gewinner</h2>
          <pre>{{ gewinner_inhalt }}</pre>
          <form method="post">
            <input type="hidden" name="action" value="reset_gewinner">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button type="submit">Gewinner zurücksetzen</button>
          </form>
        </section>
      </div>

      <section class="panel">
        <h2>QR-Codes</h2>
        {% if qr_files %}
          <div class="qr-list">
            {% for file in qr_files %}
              <div class="qr-card">
                <img src="/qr_codes/{{ file }}" alt="{{ file }}">
                <div>{{ file }}</div>
              </div>
            {% endfor %}
          </div>
        {% else %}
          <p>Keine QR-Codes vorhanden.</p>
        {% endif %}
        <form method="post">
          <input type="hidden" name="action" value="reset_qr_codes">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <button type="submit">QR-Codes löschen</button>
        </form>
      </section>

    <footer>
      <div class="footer-inner">&copy; 2023 - 2025 Erik Schauer, DO1FFE, do1ffe@darc.de</div>
    </footer>
    </main>
  </body>
</html>
'''

if __name__ == '__main__':
    if not os.path.exists('qr_codes'):
        os.makedirs('qr_codes')

    load_prizes()

    if DEBUG: logging.debug("Starte Flask-App")
    app.run(host='0.0.0.0', port=8087, debug=DEBUG)
