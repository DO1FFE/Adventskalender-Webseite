# Erik Schauer, DO1FFE, do1ffe@darc.de
# Adventskalender Programm mit Webserver, Cookie-Unterstützung, farbigen Türchen und QR-Code Download
# Erstelldatum: 28.11.2023

from flask import Flask, request, make_response, render_template_string, send_from_directory
import datetime
import random
import qrcode
import os

app = Flask(__name__)

# Initialisierung
tuerchen_status = {tag: set() for tag in range(1, 25)}  # Speichert, welche Benutzer ein Türchen schon geöffnet haben
max_preise = 10  # Maximale Anzahl an Preisen
gewinn_zeiten = [12, 13, 14, 15, 16, 17, 18, 19, 20, 21]  # Mögliche Uhrzeiten für die Gewinnvergabe
tuerchen_farben = ["#FFCCCC", "#CCFFCC", "#CCCCFF", "#FFFFCC", "#CCFFFF", "#FFCCFF", "#FFCC99", "#99CCFF", "#FF9999", "#99FF99", "#9999FF", "#FF9966"] * 2  # Farben für die Türchen

def anzahl_vergebener_preise():
    if os.path.exists("gewinner.txt"):
        with open("gewinner.txt", "r") as file:
            return len(file.readlines())
    return 0

def hat_teilgenommen(benutzername, tag):
    with open("teilnehmer.txt", "r") as file:
        teilnahmen = file.readlines()
    return f"{benutzername}-{tag}\n" in teilnahmen

def speichere_teilnehmer(benutzername, tag):
    with open("teilnehmer.txt", "a") as file:
        file.write(f"{benutzername}-{tag}\n")

def speichere_gewinner(benutzername, tag):
    with open("gewinner.txt", "a") as file:
        file.write(f"{benutzername} - Tag {tag} - OV L11 - 2023\n")

@app.route('/', methods=['GET', 'POST'])
def startseite():
    username = request.cookies.get('username')
    if request.method == 'POST' and not username:
        username = request.form['username']
        resp = make_response(render_template_string(HOME_PAGE, username=username, tuerchen=tuerchen_reihenfolge, heute=datetime.date.today(), tuerchen_status=tuerchen_status))
        resp.set_cookie('username', username)
        return resp
    else:
        return render_template_string(HOME_PAGE, username=username, tuerchen=tuerchen_reihenfolge, heute=datetime.date.today(), tuerchen_status=tuerchen_status)

@app.route('/oeffne_tuerchen/<int:tag>', methods=['GET'])
def oeffne_tuerchen(tag):
    benutzername = request.cookies.get('username')
    if not benutzername:
        return "Bitte gib zuerst deinen Benutzernamen auf der Startseite ein."

    heute = datetime.date.today()
    aktuelle_stunde = datetime.datetime.now().hour
    if heute.month == 12 and heute.day == tag:
        benutzername = benutzername.upper()
        
        if hat_teilgenommen(benutzername, tag):
            return "Du hast dieses Türchen heute bereits geöffnet!"

        speichere_teilnehmer(benutzername, tag)
        tuerchen_status[tag].add(benutzername)

        vergebene_preise = anzahl_vergebener_preise()
        if vergebene_preise < max_preise and aktuelle_stunde in gewinn_zeiten and random.choice([True, False]):
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
            qr_filename = f"{benutzername}_{tag}.png"
            img.save(f"qr_codes/{qr_filename}")

            return f"Glückwunsch! Du hast ein Freigetränk in der Clubstation des OV L11 gewonnen. <a href='/download_qr/{qr_filename}'>Lade deinen QR-Code herunter</a> oder sieh ihn dir <a href='/qr_codes/{qr_filename}'>hier an</a>."

        return "Du hattest heute leider kein Glück, versuche es morgen noch einmal!"

    else:
        return "Dieses Türchen kann heute noch nicht geöffnet werden."

@app.route('/download_qr/<filename>', methods=['GET'])
def download_qr(filename):
    return send_from_directory(directory='qr_codes', filename=filename, as_attachment=True)

HOME_PAGE = '''
<!doctype html>
<html lang="en">
  <head>
    <title>Adventskalender</title>
    <style>
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
      }
      .disabled {
        filter: grayscale(100%);
      }
    </style>
  </head>
  <body>
    <h1>Adventskalender des OV L11</h1>
    <p>Jeden Tag hast du die Chance, ein Freigetränk in unserer Clubstation zu gewinnen. Viel Glück!</p>
    {% if not username %}
      <form method="post">
        <label for="username">Dein Name:</label>
        <input type="text" id="username" name="username">
        <button type="submit">Name setzen</button>
      </form>
    {% else %}
      <p>Willkommen, {{ username }}!</p>
      <div>
        {% for num in tuerchen %}
          <a href="{% if not tuerchen_status[num] and num <= heute.day %}/oeffne_tuerchen/{{ num }}{% else %}#{% endif %}" class="tuerchen{% if tuerchen_status[num] or num > heute.day %} disabled{% endif %}" style="background-color: {{ tuerchen_farben[num-1] }}">
            {{ num }}
          </a>
        {% endfor %}
      </div>
    {% endif %}
  </body>
</html>
'''

if __name__ == '__main__':
    if not os.path.exists('qr_codes'):
        os.makedirs('qr_codes')
    app.run(debug=True, port=8087)
