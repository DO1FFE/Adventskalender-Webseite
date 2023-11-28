# Adventskalender für OV L11

Dieses Repository enthält den Code für einen digitalen Adventskalender, speziell entwickelt für den OV L11. Der Kalender ermöglicht es Benutzern, täglich ein Türchen zu öffnen und die Chance auf ein Freigetränk in der Clubstation zu haben.

## Funktionsweise

- Jeder Benutzer kann einmal pro Tag ein Türchen öffnen.
- Jeden Tag wird zufällig entschieden, ob ein Preis (Freigetränk) vergeben wird.
- Die Gewinnchancen verteilen sich über den Tag, wobei die Vergabe der Preise nach bestimmten Uhrzeiten erfolgt.
- Insgesamt werden im Laufe des Dezembers 10 Freigetränke vergeben.
- Gewinner erhalten einen QR-Code, der als Berechtigungsnachweis dient.

## Technologie

- **Backend**: Flask (Python)
- **Datenverwaltung**: Einfache Textdateien (`teilnehmer.txt`, `gewinner.txt`)
- **QR-Code-Generierung**: Python `qrcode` Bibliothek

## Setup

1. Stellen Sie sicher, dass Python auf Ihrem System installiert ist.
2. Installieren Sie Flask und die `qrcode`-Bibliothek:
   ```bash
   pip install Flask qrcode[pil]
   ```
3. Klonen Sie das Repository und navigieren Sie in das Projektverzeichnis.
4. Starten Sie den Server:
   ```bash
   python app.py
   ```
5. Öffnen Sie einen Webbrowser und gehen Sie zu `http://localhost:8087/`.

## Konfiguration

- Sie können die Uhrzeiten für die Gewinnvergabe in der Datei `app.py` anpassen.
- Die Farben der Türchen können ebenfalls in `app.py` geändert werden.

## Sicherheitshinweise

- Dieses Projekt ist ein Demonstrationsprojekt und sollte nicht in einer Produktionsumgebung ohne weitere Sicherheitsmaßnahmen eingesetzt werden.
