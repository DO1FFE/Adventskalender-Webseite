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
from markupsafe import Markup, escape
from werkzeug.security import generate_password_hash, check_password_hash

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

USER_DATABASE = "users.db"
ADMIN_EMAIL = "do1ffe@darc.de"


def get_db_connection():
    connection = sqlite3.connect(USER_DATABASE)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
    except sqlite3.DatabaseError as exc:
        logging.error("PRAGMA foreign_keys konnte nicht gesetzt werden: %s", exc)
    return connection


def init_user_db():
    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    email TEXT UNIQUE,
                    display_name TEXT,
                    password_hash TEXT
                )
                """
            )
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


def normalise_email(email):
    if email is None:
        return ""
    return email.strip().lower()


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


def create_user(email, display_name, password):
    email_normalised = normalise_email(email)
    if not email_normalised or not password:
        raise ValueError("E-Mail-Adresse und Passwort dürfen nicht leer sein.")
    display_name = (display_name or email_normalised).strip()
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
        name_part, amount_part = map(str.strip, stripped.split("=", 1))
        if not name_part:
            raise ValueError(f"Zeile {idx}: Preisname fehlt.")
        if not amount_part:
            raise ValueError(f"Zeile {idx}: Anzahl fehlt.")
        sponsor = ""
        sponsor_link = ""
        if "|" in name_part:
            name_part, sponsor_part = map(str.strip, name_part.split("|", 1))
            potential_sponsor = sponsor_part
            if potential_sponsor.endswith(")") and "(" in potential_sponsor:
                base, link_candidate = potential_sponsor.rsplit("(", 1)
                link_candidate = link_candidate.rstrip(")").strip()
                if link_candidate and link_candidate.lower().startswith(("http://", "https://")):
                    sponsor = base.strip()
                    sponsor_link = link_candidate
                else:
                    sponsor = potential_sponsor
            else:
                sponsor = potential_sponsor
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
def login():
    if session.get('user_id'):
        return redirect(url_for('startseite'))

    error = ""
    email = ""
    message = ""
    if request.method == 'GET' and request.args.get('registered'):
        message = "Dein Konto wurde erfolgreich erstellt. Bitte melde dich jetzt an."

    if request.method == 'POST':
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
def register():
    if session.get('user_id'):
        return redirect(url_for('startseite'))

    error = ""
    email = ""
    display_name = ""

    if request.method == 'POST':
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
    return redirect(url_for('login'))


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
        padding-bottom: 80px;
      }
      body::before,
      body::after {
        content: "";
        position: fixed;
        top: -10%;
        left: -10%;
        width: 120%;
        height: 120%;
        background-repeat: repeat;
        background-image: radial-gradient(2px 2px at 20px 20px, rgba(255,255,255,0.85) 50%, transparent 52%),
                          radial-gradient(3px 3px at 70px 50px, rgba(255,255,255,0.7) 50%, transparent 52%),
                          radial-gradient(1.5px 1.5px at 150px 120px, rgba(255,255,255,0.9) 50%, transparent 52%),
                          radial-gradient(2.5px 2.5px at 100px 180px, rgba(255,255,255,0.8) 50%, transparent 52%),
                          radial-gradient(2px 2px at 40px 200px, rgba(255,255,255,0.65) 50%, transparent 52%),
                          radial-gradient(1.5px 1.5px at 180px 80px, rgba(255,255,255,0.95) 50%, transparent 52%);
        background-size: 220px 220px, 240px 240px, 260px 260px, 210px 210px, 200px 200px, 230px 230px;
        background-position: 0 0, 50px 80px, 120px 30px, 80px 150px, 10px 100px, 140px 60px;
        animation: snow 18s linear infinite;
        opacity: 0.6;
        pointer-events: none;
      }
      body::after {
        animation-duration: 28s;
        opacity: 0.45;
        background-image: radial-gradient(1.5px 1.5px at 40px 30px, rgba(255,255,255,0.75) 50%, transparent 52%),
                          radial-gradient(2px 2px at 90px 90px, rgba(255,255,255,0.55) 50%, transparent 52%),
                          radial-gradient(1px 1px at 130px 70px, rgba(255,255,255,0.85) 50%, transparent 52%),
                          radial-gradient(2px 2px at 160px 160px, rgba(255,255,255,0.65) 50%, transparent 52%),
                          radial-gradient(1.8px 1.8px at 20px 150px, rgba(255,255,255,0.7) 50%, transparent 52%),
                          radial-gradient(1.2px 1.2px at 110px 10px, rgba(255,255,255,0.9) 50%, transparent 52%);
        background-size: 200px 200px, 220px 220px, 240px 240px, 210px 210px, 190px 190px, 230px 230px;
        background-position: 20px 40px, 80px 0, 140px 90px, 60px 180px, 10px 120px, 100px 30px;
      }
      @keyframes snow {
        from { transform: translate3d(-3%, -10%, 0); }
        to { transform: translate3d(3%, 100%, 0); }
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
          grid-template-columns: repeat(2, minmax(0, 1fr));
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
      }
      .prize-list li {
        margin-bottom: 8px;
        color: #ffeecf;
        font-weight: 600;
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
        padding: 30px 20px 140px;
        position: relative;
        z-index: 1;
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
      <p>Stell jeden Tag ein neues Türchen frei, genieße die winterliche Vorfreude und sichere dir mit etwas Glück {% if prize_phrase %}einen unserer festlichen Preise wie {{ prize_phrase }}{% else %}einen festlichen Preis{% endif %} in unserer festlich geschmückten Clubstation!</p>
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
                  {% if prize.get('sponsor') %}
                    – Sponsor:
                    {% if prize.get('sponsor_link') %}
                      <a href="{{ prize.get('sponsor_link') }}" target="_blank" rel="noopener noreferrer">{{ prize.get('sponsor') }}</a>
                    {% else %}
                      {{ prize.get('sponsor') }}
                    {% endif %}
                  {% endif %}
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
    <footer>
      <div class="footer-inner">
        <p>&copy; 2023 - 2025 Erik Schauer, DO1FFE, do1ffe@darc.de</p>
      </div>
    </footer>
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
        padding-bottom: 80px;
      }
      body::before,
      body::after {
        content: "";
        position: fixed;
        top: -10%;
        left: -10%;
        width: 120%;
        height: 120%;
        background-repeat: repeat;
        background-image: radial-gradient(2px 2px at 20px 20px, rgba(255,255,255,0.85) 50%, transparent 52%),
                          radial-gradient(3px 3px at 70px 50px, rgba(255,255,255,0.7) 50%, transparent 52%),
                          radial-gradient(1.5px 1.5px at 150px 120px, rgba(255,255,255,0.9) 50%, transparent 52%),
                          radial-gradient(2.5px 2.5px at 100px 180px, rgba(255,255,255,0.8) 50%, transparent 52%),
                          radial-gradient(2px 2px at 40px 200px, rgba(255,255,255,0.65) 50%, transparent 52%),
                          radial-gradient(1.5px 1.5px at 180px 80px, rgba(255,255,255,0.95) 50%, transparent 52%);
        background-size: 220px 220px, 240px 240px, 260px 260px, 210px 210px, 200px 200px, 230px 230px;
        background-position: 0 0, 50px 80px, 120px 30px, 80px 150px, 10px 100px, 140px 60px;
        animation: snow 18s linear infinite;
        opacity: 0.6;
        pointer-events: none;
      }
      body::after {
        animation-duration: 28s;
        opacity: 0.45;
        background-image: radial-gradient(1.5px 1.5px at 40px 30px, rgba(255,255,255,0.75) 50%, transparent 52%),
                          radial-gradient(2px 2px at 90px 90px, rgba(255,255,255,0.55) 50%, transparent 52%),
                          radial-gradient(1px 1px at 130px 70px, rgba(255,255,255,0.85) 50%, transparent 52%),
                          radial-gradient(2px 2px at 160px 160px, rgba(255,255,255,0.65) 50%, transparent 52%),
                          radial-gradient(1.8px 1.8px at 20px 150px, rgba(255,255,255,0.7) 50%, transparent 52%),
                          radial-gradient(1.2px 1.2px at 110px 10px, rgba(255,255,255,0.9) 50%, transparent 52%);
        background-size: 200px 200px, 220px 220px, 240px 240px, 210px 210px, 190px 190px, 230px 230px;
        background-position: 20px 40px, 80px 0, 140px 90px, 60px 180px, 10px 120px, 100px 30px;
      }
      @keyframes snow {
        from { transform: translate3d(-3%, -10%, 0); }
        to { transform: translate3d(3%, 100%, 0); }
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
        z-index: 1;
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
    <footer>
      <div class="footer-inner">
        <p>&copy; 2023 - 2025 Erik Schauer, DO1FFE, do1ffe@darc.de</p>
      </div>
    </footer>
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
        padding-bottom: 8rem;
        background: #f5f7fa;
        font-family: 'Open Sans', Arial, sans-serif;
        color: #1b2a35;
      }
      main {
        max-width: 960px;
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
        width: min(100%, 960px);
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

      <div class="grid">
        <section class="panel">
          <h2>Teilnehmer</h2>
          <pre>{{ teilnehmer_inhalt }}</pre>
          <form method="post">
            <input type="hidden" name="action" value="reset_teilnehmer">
            <button type="submit">Teilnehmer zurücksetzen</button>
          </form>
        </section>
        <section class="panel">
          <h2>Gewinner</h2>
          <pre>{{ gewinner_inhalt }}</pre>
          <form method="post">
            <input type="hidden" name="action" value="reset_gewinner">
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
