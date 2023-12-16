# Erik Schauer, DO1FFE, do1ffe@darc.de
# Adventskalender Programm mit Webserver, Cookie-Unterstützung, farbigen Türchen und QR-Code Download
# Erstelldatum: 28.11.2023

import logging
import datetime
import random
import qrcode
import os
import pytz
from flask import Flask, request, make_response, render_template_string, send_from_directory, Markup

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

# HTML-Templates mit Header und Footer
HOME_PAGE = '''
<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Adventskalender</title>
    <style>
      body { font-family: Arial, sans-serif; }
      header, footer { padding: 10px; background-color: #f1f1f1; text-align: center; }
      nav a { margin-right: 15px; }
      .tuerchen { 
        display: inline-block;
        width: 100px;
        height: 100px;
        margin: 10px;
        text-align: center;
        vertical-align: middle;
        line-height: 100px;
        border-radius: 10px;
        font-size: 20px;
        font-weight: bold;
        color: black;
        text-decoration: none;
      }
      .disabled { 
        filter: grayscale(100%);
        pointer-events: none;
        cursor: default;
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
    <h1>Adventskalender des OV L11</h1>
    <p>Jeden Tag hast du die Chance auf ein Freigetränk in unserer Clubstation. Viel Glück!</p>
    {% if not username %}
      <form method="post">
        <label for="username">Dein vollständiger Name oder Rufzeichen:</label>
        <input type="text" id="username" name="username" required>
        <button type="submit">Name/Rufzeichen setzen</button>
      </form>
    {% else %}
      <p>Willkommen, {{ username }}!</p>
      <div>
        {% for num in tuerchen %}
          <a href="{% if not tuerchen_status[num] and num >= heute.day %}/oeffne_tuerchen/{{ num }}{% else %}#{% endif %}" class="tuerchen{% if tuerchen_status[num] or num < heute.day %} disabled{% endif %}" style="background-color: {{ tuerchen_farben[num-1] }}">
            {{ num }}
          </a>
        {% endfor %}
      </div>
    {% endif %}
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
      body { font-family: Arial, sans-serif; }
      header, footer { padding: 10px; background-color: #f1f1f1; text-align: center; }
      nav a { margin-right: 15px; }
    </style>
  </head>
  <body>
    <header>
      <nav>
        <a href="/">Zurück zum Adventskalender</a>
      </nav>
    </header>
    <div>{{ content }}</div>
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
      body { font-family: Arial, sans-serif; }
      header, footer { padding: 10px; background-color: #f1f1f1; text-align: center; }
      nav a { margin-right: 15px; }
      .qr-image { margin: 10px; }
      .qr-filename { text-align: center; }
      .data-section { margin: 20px 0; }
      .data-title { font-weight: bold; }
      .data-content { background-color: #f1f1f1; padding: 10px; }
    </style>
  </head>
  <body>
    <header>
      <nav>
        <a href="/">Zurück zum Adventskalender</a>
      </nav>
    </header>
    <h1>QR-Codes</h1>
    <div>
      {% for file in qr_files %}
        <div class="qr-image">
          <img src="/qr_codes/{{ file }}" alt="{{ file }}" width="100" height="100">
          <p class="qr-filename">{{ file }}</p>
        </div>
      {% endfor %}
    </div>
    <div class="data-section">
      <h2 class="data-title">Teilnehmer</h2>
      <pre class="data-content">{{ teilnehmer_inhalt }}</pre>
    </div>
    <div class="data-section">
      <h2 class="data-title">Gewinner</h2>
      <pre class="data-content">{{ gewinner_inhalt }}</pre>
    </div>
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
