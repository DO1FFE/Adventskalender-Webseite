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
gewinn_zeiten = [12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
tuerchen_farben = ["#FFCCCC", "#CCFFCC", "#CCCCFF", "#FFFFCC", "#CCFFFF", "#FFCCFF", "#FFCC99", "#99CCFF", "#FF9999", "#99FF99", "#9999FF", "#FF9966"] * 2

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
                total = int(entry.get("total", entry.get("quantity", 0)))
                remaining = int(entry.get("remaining", total))
                total = max(total, 0)
                remaining = max(min(remaining, total), 0)
                if name and total > 0:
                    prizes.append({"name": name, "total": total, "remaining": remaining})
            if prizes:
                return prizes
        except (json.JSONDecodeError, ValueError, OSError, TypeError) as exc:
            logging.error("Fehler beim Laden der Preise: %s", exc)
    default_prizes = [{"name": "Freigetränk", "total": 15, "remaining": 15}]
    save_prizes(default_prizes)
    return default_prizes


def get_prize_stats(prizes=None):
    if prizes is None:
        prizes = load_prizes()
    total = sum(prize.get("total", 0) for prize in prizes)
    remaining = sum(prize.get("remaining", 0) for prize in prizes)
    awarded = total - remaining
    return prizes, total, remaining, awarded


def reduce_prize(prizes):
    available = [prize for prize in prizes if prize.get("remaining", 0) > 0]
    if not available:
        return None
    weights = [prize["remaining"] for prize in available]
    selected = random.choices(available, weights=weights, k=1)[0]
    selected["remaining"] -= 1
    save_prizes(prizes)
    return selected["name"]


def format_prize_lines(prizes):
    lines = []
    for prize in prizes:
        total = prize.get("total", 0)
        remaining = prize.get("remaining", total)
        name = prize.get("name", "")
        if remaining != total:
            lines.append(f"{name}={total}/{remaining}")
        else:
            lines.append(f"{name}={total}")
    return "\n".join(lines)


def parse_prize_configuration(prize_data):
    prizes = []
    for idx, line in enumerate(prize_data.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if "=" not in stripped:
            raise ValueError(f"Zeile {idx}: Bitte das Format 'Name=Anzahl' verwenden.")
        name, amount_part = map(str.strip, stripped.split("=", 1))
        if not name:
            raise ValueError(f"Zeile {idx}: Preisname fehlt.")
        if not amount_part:
            raise ValueError(f"Zeile {idx}: Anzahl fehlt.")
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
        prizes.append({"name": name, "total": total, "remaining": remaining})
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

def speichere_gewinner(benutzername, tag, preis, jahr=None):
    if DEBUG: logging.debug(f"Speichere Gewinner {benutzername} für Tag {tag} ({preis})")
    if jahr is None:
        jahr = get_local_datetime().year
    with open("gewinner.txt", "a", encoding="utf-8") as file:
        file.write(f"{benutzername} - Tag {tag} - {preis} - OV L11 - {jahr}\n")

@app.route('/', methods=['GET', 'POST'])
def startseite():
    username = request.cookies.get('username')
    if DEBUG: logging.debug(f"Startseite aufgerufen - Username: {username}")

    heute = get_local_datetime().date()
    if DEBUG: logging.debug(f"Startseite - Heute: {heute}")

    _, max_preise, verbleibende_preise, _ = get_prize_stats()

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
            preis_name = reduce_prize(prizes)
            if not preis_name:
                if DEBUG: logging.debug("Preis konnte nicht reduziert werden")
                return make_response(render_template_string(GENERIC_PAGE, content="Alle Preise wurden bereits vergeben."))
            aktuelles_jahr = heute.year
            speichere_gewinner(benutzername, tag, preis_name, jahr=aktuelles_jahr)
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
            img.save(os.path.join('qr_codes', qr_filename))  # Speicherort korrigiert
            if DEBUG: logging.debug(f"QR-Code generiert und gespeichert: {qr_filename}")
            content = Markup(
                f"Glückwunsch! Du hast {preis_name} gewonnen. "
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
    return send_from_directory(directory='qr_codes', filename=filename, as_attachment=True)

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
        background-image: radial-gradient(2px 2px at 20px 20px, rgba(255,255,255,0.8) 50%, transparent 50%),
                          radial-gradient(3px 3px at 70px 50px, rgba(255,255,255,0.6) 50%, transparent 50%),
                          radial-gradient(1.5px 1.5px at 150px 120px, rgba(255,255,255,0.9) 50%, transparent 50%);
        animation: snow 18s linear infinite;
        opacity: 0.6;
        pointer-events: none;
      }
      body::after {
        animation-duration: 28s;
        opacity: 0.4;
        background-image: radial-gradient(1.5px 1.5px at 40px 30px, rgba(255,255,255,0.7) 50%, transparent 50%),
                          radial-gradient(2px 2px at 90px 90px, rgba(255,255,255,0.5) 50%, transparent 50%),
                          radial-gradient(1px 1px at 130px 70px, rgba(255,255,255,0.9) 50%, transparent 50%);
      }
      @keyframes snow {
        from { transform: translateY(-10%); }
        to { transform: translateY(100%); }
      }
      header, footer {
        padding: 18px;
        background: rgba(15, 46, 72, 0.85);
        text-align: center;
        border-bottom: 2px solid rgba(255, 255, 255, 0.2);
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.4);
      }
      footer {
        border-bottom: none;
        border-top: 2px solid rgba(255, 255, 255, 0.2);
        position: fixed;
        bottom: 0;
        width: 100%;
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
      .welcome {
        text-align: center;
        font-size: 1.2rem;
        margin-bottom: 20px;
        color: #ffeecf;
      }
      .tuerchen-container {
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 10px;
        padding-bottom: 80px;
      }
      .tuerchen {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 110px;
        height: 110px;
        margin: 10px;
        border-radius: 12px;
        font-size: 26px;
        font-weight: 700;
        color: #1f2a44;
        text-decoration: none;
        border: 2px solid rgba(255, 255, 255, 0.7);
        box-shadow: 0 10px 20px rgba(0, 0, 0, 0.35);
        transition: transform 0.2s ease, box-shadow 0.2s ease, filter 0.3s ease;
        background-blend-mode: screen;
      }
      .tuerchen:hover {
        transform: translateY(-6px);
        box-shadow: 0 14px 24px rgba(0, 0, 0, 0.45);
      }
      .disabled {
        filter: grayscale(100%) brightness(0.8);
        pointer-events: none;
        cursor: default;
        opacity: 0.7;
        transform: none;
        box-shadow: none;
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
      <h1>Adventskalender des OV L11</h1>
      <p>Stell jeden Tag ein neues Türchen frei, genieße die winterliche Vorfreude und sichere dir mit etwas Glück ein Freigetränk in unserer festlich geschmückten Clubstation!</p>
      {% if not username %}
        <form method="post">
          <label for="username">Dein vollständiger Name oder Rufzeichen:</label>
          <input type="text" id="username" name="username" required>
          <button type="submit">Name/Rufzeichen setzen</button>
        </form>
      {% else %}
        <div class="welcome">Willkommen zurück, {{ username }}! Viel Glück beim heutigen Türchen.</div>
        <div class="tuerchen-container">
          {% for num in tuerchen %}
            <a href="{% if not tuerchen_status[num] and num >= heute.day %}/oeffne_tuerchen/{{ num }}{% else %}#{% endif %}" class="tuerchen{% if tuerchen_status[num] or num < heute.day %} disabled{% endif %}" style="background-color: {{ tuerchen_farben[num-1] }}">
              {{ num }}
            </a>
          {% endfor %}
        </div>
      {% endif %}
    </main>
    <footer>
      <p>&copy; 2023 - 2025 Erik Schauer, DO1FFE, do1ffe@darc.de</p>
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
        background-image: radial-gradient(2px 2px at 20px 20px, rgba(255,255,255,0.8) 50%, transparent 50%),
                          radial-gradient(3px 3px at 70px 50px, rgba(255,255,255,0.6) 50%, transparent 50%),
                          radial-gradient(1.5px 1.5px at 150px 120px, rgba(255,255,255,0.9) 50%, transparent 50%);
        animation: snow 18s linear infinite;
        opacity: 0.6;
        pointer-events: none;
      }
      body::after {
        animation-duration: 28s;
        opacity: 0.4;
        background-image: radial-gradient(1.5px 1.5px at 40px 30px, rgba(255,255,255,0.7) 50%, transparent 50%),
                          radial-gradient(2px 2px at 90px 90px, rgba(255,255,255,0.5) 50%, transparent 50%),
                          radial-gradient(1px 1px at 130px 70px, rgba(255,255,255,0.9) 50%, transparent 50%);
      }
      @keyframes snow {
        from { transform: translateY(-10%); }
        to { transform: translateY(100%); }
      }
      header, footer {
        padding: 18px;
        background: rgba(15, 46, 72, 0.85);
        text-align: center;
        border-bottom: 2px solid rgba(255, 255, 255, 0.2);
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.4);
      }
      footer {
        border-bottom: none;
        border-top: 2px solid rgba(255, 255, 255, 0.2);
        position: fixed;
        bottom: 0;
        width: 100%;
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
      <p>&copy; 2023 - 2025 Erik Schauer, DO1FFE, do1ffe@darc.de</p>
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

    if request.method == 'POST':
        raw_prizes = request.form.get('prize_data', '')
        try:
            prizes = parse_prize_configuration(raw_prizes)
            save_prizes(prizes)
            message = "Preise wurden aktualisiert."
        except ValueError as exc:
            is_error = True
            message = str(exc)
        prizes = load_prizes()

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
        margin-top: 2rem;
        text-align: center;
        color: #6c7a89;
        font-size: 0.9rem;
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
        <h2>Preise verwalten</h2>
        <p>Eintrag pro Zeile im Format <code>Name=Gesamt</code> oder <code>Name=Gesamt/Verfügbar</code>. Zeilen mit Anzahl 0 werden ignoriert.</p>
        <form method="post">
          <textarea name="prize_data" id="prize_data">{{ prize_lines }}</textarea>
          <button type="submit">Preise speichern</button>
        </form>
        <p><strong>Gesamtpreise:</strong> {{ total_prizes }} &middot; <strong>Bereits vergeben:</strong> {{ awarded_prizes }} &middot; <strong>Noch verfügbar:</strong> {{ remaining_prizes }}</p>
        <ul>
          {% for prize in prizes %}
            <li><strong>{{ prize.name }}</strong>: {{ prize.remaining }} von {{ prize.total }} verfügbar</li>
          {% endfor %}
        </ul>
      </section>

      <div class="grid">
        <section class="panel">
          <h2>Teilnehmer</h2>
          <pre>{{ teilnehmer_inhalt }}</pre>
        </section>
        <section class="panel">
          <h2>Gewinner</h2>
          <pre>{{ gewinner_inhalt }}</pre>
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
      </section>

    <footer>&copy; 2023 - 2025 Erik Schauer, DO1FFE, do1ffe@darc.de</footer>
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
