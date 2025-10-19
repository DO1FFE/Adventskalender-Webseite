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
from flask import Flask, request, make_response, render_template_string, send_from_directory
from markupsafe import Markup

# Logging-Konfiguration
logging.basicConfig(filename='debug.log', level=logging.DEBUG, 
                    format='%(asctime)s %(levelname)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

# Debugging-Flag
DEBUG = True

# Lokale Zeitzone festlegen
local_timezone = pytz.timezone("Europe/Berlin")

app = Flask(__name__)

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
                    }
                    prizes.append(prize_entry)
            if prizes:
                return prizes
        except (json.JSONDecodeError, ValueError, OSError, TypeError) as exc:
            logging.error("Fehler beim Laden der Preise: %s", exc)
    default_prizes = [
        {"name": "Freigetränk", "total": 15, "remaining": 15, "sponsor": ""}
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
        name_segment = name
        if sponsor:
            name_segment = f"{name} | {sponsor}"
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
                f"Zeile {idx}: Bitte das Format 'Name=Anzahl' oder 'Name | Sponsor=Anzahl' verwenden."
            )
        name_part, amount_part = map(str.strip, stripped.split("=", 1))
        if not name_part:
            raise ValueError(f"Zeile {idx}: Preisname fehlt.")
        if not amount_part:
            raise ValueError(f"Zeile {idx}: Anzahl fehlt.")
        sponsor = ""
        if "|" in name_part:
            name_part, sponsor_part = map(str.strip, name_part.split("|", 1))
            sponsor = sponsor_part
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
        }
        prizes.append(prize_entry)
    if not prizes:
        raise ValueError("Es muss mindestens ein Preis mit positiver Anzahl angegeben werden.")
    return prizes

def hat_gewonnen(benutzername):
    """ Überprüft, ob der Benutzer bereits gewonnen hat. """
    if not os.path.exists("gewinner.txt"):
        return False
    with open("gewinner.txt", "r", encoding="utf-8") as file:
        gewinne = file.readlines()
    return any(benutzername in gewinn for gewinn in gewinne)

def gewinnchance_ermitteln(benutzername, heutiges_datum, verbleibende_preise):
    """
    Berechnet die Gewinnchance basierend auf dem aktuellen Datum, der verfügbaren Anzahl der Preise
    und ob der Benutzer bereits gewonnen hat.
    """
    verbleibende_tage = 25 - heutiges_datum.day
    if verbleibende_tage <= 0 or verbleibende_preise <= 0:
        return 0

    gewinnchance = verbleibende_preise / verbleibende_tage

    # Reduzierte Gewinnchance für Benutzer, die bereits gewonnen haben
    if hat_gewonnen(benutzername):
        return gewinnchance * 0.1  # Beispiel: 10% der normalen Gewinnchance

    return gewinnchance

def hat_teilgenommen(benutzername, tag):
    if not os.path.exists("teilnehmer.txt"):
        return False
    with open("teilnehmer.txt", "r", encoding="utf-8") as file:
        teilnahmen = file.readlines()
    return f"{benutzername}-{tag}\n" in teilnahmen

def speichere_teilnehmer(benutzername, tag):
    if DEBUG: logging.debug(f"Speichere Teilnehmer {benutzername} für Tag {tag}")
    with open("teilnehmer.txt", "a", encoding="utf-8") as file:
        file.write(f"{benutzername}-{tag}\n")

def speichere_gewinner(benutzername, tag, preis, jahr=None, sponsor=None):
    if DEBUG: logging.debug(f"Speichere Gewinner {benutzername} für Tag {tag} ({preis})")
    if jahr is None:
        jahr = get_local_datetime().year
    sponsor_text = ""
    if sponsor:
        sponsor_text = f" - Sponsor: {str(sponsor).strip()}"
    with open("gewinner.txt", "a", encoding="utf-8") as file:
        file.write(f"{benutzername} - Tag {tag} - {preis}{sponsor_text} - OV L11 - {jahr}\n")

@app.route('/', methods=['GET', 'POST'])
def startseite():
    username = request.cookies.get('username')
    if DEBUG: logging.debug(f"Startseite aufgerufen - Username: {username}")

    calendar_active = get_calendar_active()
    heute = get_local_datetime().date()
    if DEBUG: logging.debug(f"Startseite - Heute: {heute}")

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
    if username:
        for tag in range(1, 25):
            if hat_teilgenommen(username, tag):
                tuerchen_status[tag].add(username)

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
    }

    if request.method == 'POST' and not username:
        username = request.form['username'].upper()
        context["username"] = username
        resp = make_response(render_template_string(HOME_PAGE, **context))
        resp.set_cookie('username', username, max_age=2592000)
        return resp
    else:
        return render_template_string(HOME_PAGE, **context)

@app.route('/oeffne_tuerchen/<int:tag>', methods=['GET'])
def oeffne_tuerchen(tag):
    benutzername = request.cookies.get('username')
    if not get_calendar_active():
        return make_response(
            render_template_string(
                GENERIC_PAGE,
                content="Der Adventskalender ist derzeit nicht aktiv. Bitte schau später noch einmal vorbei.",
            )
        )
    if not benutzername:
        return make_response(render_template_string(GENERIC_PAGE, content="Bitte gib zuerst deinen Namen/Rufzeichen auf der Startseite ein."))

    heute = get_local_datetime().date()
    if DEBUG: logging.debug(f"Öffne Türchen {tag} aufgerufen - Benutzer: {benutzername}, Datum: {heute}")

    if heute.month == 12 and heute.day == tag:
        benutzername = benutzername.upper()

        if hat_teilgenommen(benutzername, tag):
            if DEBUG: logging.debug(f"{benutzername} hat Türchen {tag} bereits geöffnet")
            return make_response(render_template_string(GENERIC_PAGE, content="Du hast dieses Türchen heute bereits geöffnet!"))

        speichere_teilnehmer(benutzername, tag)
        tuerchen_status[tag].add(benutzername)

        prizes, max_preise, verbleibende_preise, _ = get_prize_stats()
        if verbleibende_preise <= 0:
            if DEBUG: logging.debug("Keine Preise mehr verfügbar")
            return make_response(render_template_string(GENERIC_PAGE, content="Alle Preise wurden bereits vergeben."))

        gewinnchance = gewinnchance_ermitteln(benutzername, heute, verbleibende_preise)
        if DEBUG: logging.debug(f"Gewinnchance für {benutzername} am Tag {tag}: {gewinnchance}")

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
            speichere_gewinner(
                benutzername,
                tag,
                preis_name,
                jahr=aktuelles_jahr,
                sponsor=sponsor_name,
            )
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(f"{tag}-{benutzername}-{preis_name}-OV L11-{aktuelles_jahr}")
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            qr_filename = f"{benutzername}_{tag}.png"  # Pfad korrigiert
            os.makedirs('qr_codes', exist_ok=True)
            img.save(os.path.join('qr_codes', qr_filename))  # Speicherort korrigiert
            if DEBUG: logging.debug(f"QR-Code generiert und gespeichert: {qr_filename}")
            sponsor_hint = f" – Sponsor: {sponsor_name}" if sponsor_name else ""
            content = Markup(
                f"Glückwunsch! Du hast {preis_name}{sponsor_hint} gewonnen. "
                f"<a href='/download_qr/{qr_filename}'>Lade deinen QR-Code herunter</a> "
                f"oder sieh ihn dir <a href='/qr_codes/{qr_filename}'>hier an</a>."
            )
            return make_response(render_template_string(GENERIC_PAGE, content=content))
        else:
            if DEBUG: logging.debug(f"Kein Gewinn für {benutzername} an Tag {tag}")
            return make_response(render_template_string(GENERIC_PAGE, content="Du hattest heute leider kein Glück, versuche es morgen noch einmal!"))
    else:
        if DEBUG: logging.debug(f"Türchen {tag} kann heute noch nicht geöffnet werden")
        return make_response(render_template_string(GENERIC_PAGE, content="Dieses Türchen kann heute noch nicht geöffnet werden."))

@app.route('/download_qr/<filename>', methods=['GET'])
def download_qr(filename):
    if DEBUG: logging.debug(f"Download-Anfrage für QR-Code: {filename}")
    return send_from_directory('qr_codes', filename, as_attachment=True)

@app.route('/qr_codes/<filename>')
def qr_code(filename):
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
                    – Sponsor: {{ prize.get('sponsor') }}
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
      {% if not username %}
        <form method="post">
          <label for="username">Dein vollständiger Name oder Rufzeichen:</label>
          <input type="text" id="username" name="username" required>
          <button type="submit">Name/Rufzeichen setzen</button>
        </form>
      {% else %}
        <div class="welcome">Willkommen zurück, {{ username }}! Viel Glück beim heutigen Türchen.{% if not calendar_active %}<br><strong>Hinweis:</strong> Der Adventskalender ist momentan deaktiviert. Türchen können aktuell nicht geöffnet werden.{% endif %}</div>
        <div class="calendar-board">
          <div class="calendar-header">
            <span>Dezember</span>
            <span>{{ heute.year }}</span>
          </div>
          <div class="tuerchen-container">
            {% for num in tuerchen %}
              <a href="{% if calendar_active and not tuerchen_status[num] and num >= heute.day %}/oeffne_tuerchen/{{ num }}{% else %}#{% endif %}"
                 class="tuerchen{% if not calendar_active or tuerchen_status[num] or num < heute.day %} disabled{% endif %}{% if num == heute.day %} current-day{% endif %}"
                 style="--door-color: {{ tuerchen_farben[num-1] }};">
                <span class="door-number">{{ "%02d"|format(num) }}</span>
              </a>
            {% endfor %}
          </div>
        </div>
      {% endif %}
    </main>
    <footer>
      <div class="footer-inner">
        <p>&copy; 2023 - 2025 Erik Schauer, DO1FFE, do1ffe@darc.de</p>
      </div>
    </footer>
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


@app.route('/admingeheim', methods=['GET', 'POST'])
def admin_page():
    if DEBUG: logging.debug("Admin-Seite aufgerufen")

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
        <p>Eintrag pro Zeile im Format <code>Name | Sponsor=Gesamt</code> oder <code>Name | Sponsor=Gesamt/Verfügbar</code>. Der Sponsor ist optional; Zeilen mit Anzahl 0 werden ignoriert.</p>
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
                <em>(Sponsor: {{ prize.get('sponsor') }})</em>
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
