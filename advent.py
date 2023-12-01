# Erik Schauer, DO1FFE, do1ffe@darc.de
# Adventskalender Programm mit Webserver, Cookie-Unterstützung, farbigen Türchen und QR-Code Download
# Erstelldatum: 28.11.2023

import logging
from flask import Flask, request, make_response, render_template_string, send_from_directory
import datetime
import random
import qrcode
import os

# Logging-Konfiguration
logging.basicConfig(filename='debug.log', level=logging.DEBUG, 
                    format='%(asctime)s %(levelname)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

# Debugging-Flag
DEBUG = True

app = Flask(__name__)

# Initialisierung
tuerchen_status = {tag: set() for tag in range(1, 25)} 
max_preise = 10 
gewinn_zeiten = [12, 13, 14, 15, 16, 17, 18, 19, 20, 21] 
tuerchen_farben = ["#FFCCCC", "#CCFFCC", "#CCCCFF", "#FFFFCC", "#CCFFFF", "#FFCCFF", "#FFCC99", "#99CCFF", "#FF9999", "#99FF99", "#9999FF", "#FF9966"] * 2 
tuerchen_reihenfolge = random.sample(range(1, 25), 24)

def anzahl_vergebener_preise():
    if DEBUG: logging.debug("Überprüfe Anzahl vergebener Preise")
    if os.path.exists("gewinner.txt"):
        with open("gewinner.txt", "r") as file:
            return len(file.readlines())
    return 0

def hat_teilgenommen(benutzername, tag):
    if DEBUG: logging.debug(f"Überprüfe, ob {benutzername} an Tag {tag} teilgenommen hat")
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

    heute = datetime.date.today()  
    if DEBUG: logging.debug(f"Startseite - Heute: {heute}")

    verbleibende_preise = max_preise - anzahl_vergebener_preise()

    # Türchen-Status zurücksetzen und basierend auf Teilnehmerdaten aktualisieren
    tuerchen_status.clear()
    tuerchen_status.update({tag: set() for tag in range(1, 25)})
    if username:
        for tag in range(1, 25):
            if hat_teilgenommen(username, tag):
                tuerchen_status[tag].add(username)

    if request.method == 'POST' and not username:
        username = request.form['username'].upper()
        resp = make_response(render_template_string(HOME_PAGE, username=username, tuerchen=tuerchen_reihenfolge, heute=heute, tuerchen_status=tuerchen_status, tuerchen_farben=tuerchen_farben, verbleibende_preise=verbleibende_preise))
        resp.set_cookie('username', username)
        return resp
    else:
        return render_template_string(HOME_PAGE, username=username, tuerchen=tuerchen_reihenfolge, heute=heute, tuerchen_status=tuerchen_status, tuerchen_farben=tuerchen_farben, verbleibende_preise=verbleibende_preise)

@app.route('/oeffne_tuerchen/<int:tag>', methods=['GET'])
def oeffne_tuerchen(tag):
    benutzername = request.cookies.get('username')
    if not benutzername:
        return make_response(render_template_string(GENERIC_PAGE, content="Bitte gib zuerst deinen Namen/Rufzeichen auf der Startseite ein."))

    heute = datetime.date.today()
    if DEBUG: logging.debug(f"Öffne Türchen {tag} aufgerufen - Benutzer: {benutzername}, Datum: {heute}")

    if heute.month == 12 and heute.day == tag:
        benutzername = benutzername.upper()
        
        if hat_teilgenommen(benutzername, tag):
            if DEBUG: logging.debug(f"{benutzername} hat Türchen {tag} bereits geöffnet")
            return make_response(render_template_string(GENERIC_PAGE, content="Du hast dieses Türchen heute bereits geöffnet!"))

        speichere_teilnehmer(benutzername, tag)
        tuerchen_status[tag].add(benutzername)

        vergebene_preise = anzahl_vergebener_preise()
        if vergebene_preise < max_preise and datetime.datetime.now().hour in gewinn_zeiten and random.choice([True, False]):
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
            qr_filename = f"qr_codes/{benutzername}_{tag}.png"
            img.save(qr_filename)
            if DEBUG: logging.debug(f"QR-Code generiert und gespeichert: {qr_filename}")

            return make_response(render_template_string(GENERIC_PAGE, content=f"Glückwunsch! Du hast ein Freigetränk in der Clubstation des OV L11 gewonnen. <a href='/download_qr/{qr_filename}'>Lade deinen QR-Code herunter</a> oder sieh ihn dir <a href='/qr_codes/{qr_filename}'>hier an</a>."))
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
        <div class="preise">Verbleibende Preise: {{ verbleibende_preise }}</div>
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

if __name__ == '__main__':
    if not os.path.exists('qr_codes'):
        os.makedirs('qr_codes')

    if DEBUG: logging.debug("Starte Flask-App")
    app.run(host='0.0.0.0', port=8087, debug=DEBUG)
