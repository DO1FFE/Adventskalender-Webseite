# Erik Schauer, DO1FFE, do1ffe@darc.de
# Adventskalender Programm mit Webserver, Cookie-Unterstützung, farbigen Türchen und QR-Code Download
# Erstelldatum: 28.11.2023

import logging
import datetime
import random
import qrcode
import os
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
max_preise = 15
gewinn_zeiten = [12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
tuerchen_farben = ["#FFCCCC", "#CCFFCC", "#CCCCFF", "#FFFFCC", "#CCFFFF", "#FFCCFF", "#FFCC99", "#99CCFF", "#FF9999", "#99FF99", "#9999FF", "#FF9966"] * 2

def get_local_datetime():
    utc_dt = datetime.datetime.now(pytz.utc)  # aktuelle Zeit in UTC
    return utc_dt.astimezone(local_timezone)  # konvertiere in lokale Zeitzone

def anzahl_vergebener_preise():
    if DEBUG: logging.debug("Überprüfe Anzahl vergebener Preise")
    if os.path.exists("gewinner.txt"):
        with open("gewinner.txt", "r") as file:
            return len(file.readlines())
    return 0

def hat_gewonnen(benutzername):
    """ Überprüft, ob der Benutzer bereits gewonnen hat. """
    if not os.path.exists("gewinner.txt"):
        return False
    with open("gewinner.txt", "r") as file:
        gewinne = file.readlines()
    return any(benutzername in gewinn for gewinn in gewinne)

def gewinnchance_ermitteln(benutzername, heutiges_datum, max_preise):
    """
    Berechnet die Gewinnchance basierend auf dem aktuellen Datum, der maximalen Anzahl der Preise
    und ob der Benutzer bereits gewonnen hat.
    """
    verbleibende_tage = 25 - heutiges_datum.day
    vergebene_preise = anzahl_vergebener_preise()
    noch_zu_vergebene_preise = max_preise - vergebene_preise

    if verbleibende_tage <= 0 or noch_zu_vergebene_preise <= 0:
        return 0

    gewinnchance = noch_zu_vergebene_preise / verbleibende_tage

    # Reduzierte Gewinnchance für Benutzer, die bereits gewonnen haben
    if hat_gewonnen(benutzername):
        return gewinnchance * 0.1  # Beispiel: 10% der normalen Gewinnchance

    return gewinnchance

def hat_teilgenommen(benutzername, tag):
    if not os.path.exists("teilnehmer.txt"):
        return False
    with open("teilnehmer.txt", "r") as file:
        teilnahmen = file.readlines()
    return f"{benutzername}-{tag}\n" in teilnahmen

def speichere_teilnehmer(benutzername, tag):
    if DEBUG: logging.debug(f"Speichere Teilnehmer {benutzername} für Tag {tag}")
    with open("teilnehmer.txt", "a") as file:
        file.write(f"{benutzername}-{tag}\n")

def speichere_gewinner(benutzername, tag):
    if DEBUG: logging.debug(f"Speichere Gewinner {benutzername} für Tag {tag}")
    with open("gewinner.txt", "a") as file:
        file.write(f"{benutzername} - Tag {tag} - OV L11 - 2023\n")

@app.route('/', methods=['GET', 'POST'])
def startseite():
    username = request.cookies.get('username')
    if DEBUG: logging.debug(f"Startseite aufgerufen - Username: {username}")

    heute = get_local_datetime().date()
    if DEBUG: logging.debug(f"Startseite - Heute: {heute}")

    verbleibende_preise = max_preise - anzahl_vergebener_preise()

    tuerchen_status.clear()
    tuerchen_status.update({tag: set() for tag in range(1, 25)})
    if username:
        for tag in range(1, 25):
            if hat_teilgenommen(username, tag):
                tuerchen_status[tag].add(username)

    # Zufällige Reihenfolge der Türchen bei jedem Aufruf
    tuerchen_reihenfolge = random.sample(range(1, 25), 24)

    if request.method == 'POST' and not username:
        username = request.form['username'].upper()
        resp = make_response(render_template_string(HOME_PAGE, username=username, tuerchen=tuerchen_reihenfolge, heute=heute, tuerchen_status=tuerchen_status, tuerchen_farben=tuerchen_farben, verbleibende_preise=verbleibende_preise, max_preise=max_preise))
        resp.set_cookie('username', username, max_age=2592000)
        return resp
    else:
        return render_template_string(HOME_PAGE, username=username, tuerchen=tuerchen_reihenfolge, heute=heute, tuerchen_status=tuerchen_status, tuerchen_farben=tuerchen_farben, verbleibende_preise=verbleibende_preise, max_preise=max_preise)

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

        vergebene_preise = anzahl_vergebener_preise()
        gewinnchance = gewinnchance_ermitteln(benutzername, heute, max_preise)
        if DEBUG: logging.debug(f"Gewinnchance für {benutzername} am Tag {tag}: {gewinnchance}")

        if vergebene_preise < max_preise and get_local_datetime().hour in gewinn_zeiten and random.random() < gewinnchance:
            speichere_gewinner(benutzername, tag)
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(f"{tag}-{benutzername}-OV L11-2023")
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            qr_filename = f"{benutzername}_{tag}.png"  # Pfad korrigiert
            img.save(os.path.join('qr_codes', qr_filename))  # Speicherort korrigiert
            if DEBUG: logging.debug(f"QR-Code generiert und gespeichert: {qr_filename}")
            content = Markup(f"Glückwunsch! Du hast ein Freigetränk in der Clubstation des OV L11 gewonnen. <a href='/download_qr/{qr_filename}'>Lade deinen QR-Code herunter</a> oder sieh ihn dir <a href='/qr_codes/{qr_filename}'>hier an</a>.")
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
      <p>&copy; 2023 Erik Schauer, DO1FFE, do1ffe@darc.de</p>
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
      <p>&copy; 2023 Erik Schauer, DO1FFE, do1ffe@darc.de</p>
    </footer>
  </body>
</html>
'''

# Route für die Admin-Seite hinzufügen
@app.route('/admingeheim', methods=['GET'])
def admin_page():
    if DEBUG: logging.debug("Admin-Seite aufgerufen")
    qr_files = os.listdir('qr_codes')

    # Inhalte der Dateien lesen
    if os.path.exists('teilnehmer.txt'):
        with open('teilnehmer.txt', 'r') as file:
            teilnehmer_inhalt = file.read()
    else:
        teilnehmer_inhalt = "Keine Teilnehmerdaten vorhanden."

    if os.path.exists('gewinner.txt'):
        with open('gewinner.txt', 'r') as file:
            gewinner_inhalt = file.read()
    else:
        gewinner_inhalt = "Keine Gewinnerdaten vorhanden."

    return render_template_string(ADMIN_PAGE, qr_files=qr_files, teilnehmer_inhalt=teilnehmer_inhalt, gewinner_inhalt=gewinner_inhalt)

# HTML-Template für die Admin-Seite aktualisieren
ADMIN_PAGE = '''
<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin - Adventskalender</title>
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
      main {
        position: relative;
        z-index: 1;
        max-width: 960px;
        margin: 40px auto 120px;
        background: rgba(12, 35, 52, 0.85);
        padding: 30px 40px 50px;
        border-radius: 18px;
        box-shadow: 0 12px 35px rgba(0, 0, 0, 0.45);
      }
      h1 {
        font-family: 'Mountains of Christmas', 'Open Sans', cursive;
        font-size: 2.4rem;
        text-align: center;
        margin-bottom: 25px;
        text-shadow: 0 3px 6px rgba(0, 0, 0, 0.6);
      }
      .stats-image {
        text-align: center;
        margin-bottom: 30px;
      }
      .stats-image img {
        max-width: 100%;
        border-radius: 12px;
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.4);
      }
      .qr-grid {
        display: grid;
        gap: 20px;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        margin-bottom: 30px;
      }
      .qr-card {
        background: rgba(9, 28, 44, 0.85);
        padding: 15px;
        border-radius: 12px;
        box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35);
        text-align: center;
      }
      .qr-card img {
        width: 100%;
        height: auto;
        border-radius: 8px;
      }
      .qr-filename {
        margin-top: 10px;
        font-weight: 600;
        color: #ffcf5c;
      }
      .data-section {
        margin: 25px 0;
        background: rgba(9, 28, 44, 0.85);
        border-radius: 12px;
        padding: 20px;
        box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35);
      }
      .data-title {
        font-weight: 700;
        font-size: 1.2rem;
        color: #ffcf5c;
        margin-bottom: 10px;
      }
      .data-content {
        background: rgba(12, 35, 52, 0.9);
        padding: 15px;
        border-radius: 10px;
        white-space: pre-wrap;
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
      </nav>
    </header>
    <main>
      <h1>Festliche Statistikübersicht</h1>
      <div class="stats-image">
        <img src="/event_graphen/event_graphen.png" alt="Statistiken">
      </div>
      <section>
        <h2 class="data-title">QR-Codes</h2>
        <div class="qr-grid">
          {% for file in qr_files %}
            <div class="qr-card">
              <img src="/qr_codes/{{ file }}" alt="{{ file }}">
              <div class="qr-filename">{{ file }}</div>
            </div>
          {% endfor %}
        </div>
      </section>
      <section class="data-section">
        <h2 class="data-title">Teilnehmer</h2>
        <div class="data-content">{{ teilnehmer_inhalt }}</div>
      </section>
      <section class="data-section">
        <h2 class="data-title">Gewinner</h2>
        <div class="data-content">{{ gewinner_inhalt }}</div>
      </section>
    </main>
    <footer>
      <p>&copy; 2023 Erik Schauer, DO1FFE, do1ffe@darc.de</p>
    </footer>
  </body>
</html>
'''

if __name__ == '__main__':
    if not os.path.exists('qr_codes'):
        os.makedirs('qr_codes')

    if DEBUG: logging.debug("Starte Flask-App")
    app.run(host='0.0.0.0', port=8087, debug=DEBUG)
