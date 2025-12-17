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
2. Installieren Sie Flask, `qrcode`, `pytz` und `Flask-WTF`:
   ```bash
   pip install Flask Flask-WTF qrcode pytz
   ```
3. Klonen Sie das Repository und navigieren Sie in das Projektverzeichnis.
4. Starten Sie den Server:
   ```bash
   python advent.py
   ```
5. Öffnen Sie einen Webbrowser und gehen Sie zu `http://localhost:8087/`.

> Hinweis: Die SQLite-Datenbank `users.db` wird bei Bedarf automatisch im Projektverzeichnis angelegt und ist daher nicht im Repository enthalten.

### Import von Gewinnern

- Beim Einlesen der Datei `gewinner.txt` werden vorhandene Nutzer anhand stabiler Merkmale gesucht, bevor Platzhalter-Konten angelegt werden. Dafür werden die E-Mail-Adresse (falls in der Gewinnerzeile mit `email:` hinterlegt), der Display-Name sowie optionale Einträge in `gewinner_user_mapping.json` genutzt.
- Die optionale Mapping-Datei kann entweder eine Liste oder ein Objekt mit dem Schlüssel `mappings` enthalten. Jedes Mapping unterstützt die Felder `winner_id` (alte ID aus `gewinner.txt`), `display_name`, `email` und die Ziel-`user_id`.
- Formatbeispiel:

  ```json
  {
    "mappings": [
      {"winner_id": 42, "user_id": 7},
      {"display_name": "Max Mustermann", "user_id": 5},
      {"email": "max@example.com", "user_id": 5}
    ]
  }
  ```

### Wartung: Platzhalter bereinigen

- Bereits importierte Gewinne können mit `python advent.py migrate_placeholder_rewards` auf erkannte echte Nutzer-IDs umgehängt werden. Dabei werden Platzhalter-Accounts mit `@example.invalid` entfernt, sobald keine Gewinne mehr auf sie verweisen.

## Konfiguration

- Sie können die Uhrzeiten für die Gewinnvergabe in der Datei `advent.py` anpassen.
- Die Farben der Türchen können ebenfalls in `advent.py` geändert werden.

## Sicherheitshinweise

- Dieses Projekt ist ein Demonstrationsprojekt und sollte nicht in einer Produktionsumgebung ohne weitere Sicherheitsmaßnahmen eingesetzt werden.
