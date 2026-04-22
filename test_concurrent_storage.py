import sqlite3
import threading

import advent


def _konfiguriere_temp_umgebung(tmp_path, monkeypatch):
    datenbank_pfad = tmp_path / "users.db"
    gewinner_pfad = tmp_path / "gewinner.txt"
    teilnehmer_pfad = tmp_path / "teilnehmer.txt"
    gewinner_pfad.write_text("", encoding="utf-8")
    teilnehmer_pfad.write_text("", encoding="utf-8")

    monkeypatch.setattr(advent, "USER_DATABASE", str(datenbank_pfad))
    monkeypatch.setattr(advent, "WINNERS_FILE", str(gewinner_pfad))
    monkeypatch.setattr(advent, "PARTICIPANTS_FILE", str(teilnehmer_pfad))
    advent.init_user_db()

    return datenbank_pfad


def test_konkurrierendes_speichern_von_teilnehmern(tmp_path, monkeypatch):
    datenbank_pfad = _konfiguriere_temp_umgebung(tmp_path, monkeypatch)

    fehler = []
    start_barriere = threading.Barrier(16)

    def worker(index):
        try:
            start_barriere.wait()
            user_id = str(index % 4)
            tag = (index % 6) + 1
            advent.speichere_teilnehmer(user_id, f"User {index}", tag)
        except Exception as exc:  # pragma: no cover - nur im Fehlerfall relevant
            fehler.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not fehler

    with sqlite3.connect(datenbank_pfad) as connection:
        anzahl = connection.execute("SELECT COUNT(*) FROM participant_entries").fetchone()[0]
        einzigartige = connection.execute(
            "SELECT COUNT(DISTINCT user_id || '-' || door) FROM participant_entries"
        ).fetchone()[0]
    erwartete_kombinationen = {
        (str(index % 4), (index % 6) + 1)
        for index in range(16)
    }

    assert anzahl == einzigartige
    assert anzahl == len(erwartete_kombinationen)
    assert advent.hat_teilgenommen("0", 1) is True


def test_konkurrierende_mehrfachschreibpfade_bei_gewinnern(tmp_path, monkeypatch):
    datenbank_pfad = _konfiguriere_temp_umgebung(tmp_path, monkeypatch)

    for tag in range(1, 6):
        advent.speichere_gewinner("77", "Testuser", tag, f"Preis {tag}")

    fehler = []
    start_barriere = threading.Barrier(13)

    def gewinner_worker(tag):
        try:
            start_barriere.wait()
            advent.speichere_gewinner("77", "Testuser", tag, f"Parallelpreis {tag}")
        except Exception as exc:  # pragma: no cover - nur im Fehlerfall relevant
            fehler.append(exc)

    def remove_worker():
        try:
            start_barriere.wait()
            advent.remove_user_from_winners_file("77")
        except Exception as exc:  # pragma: no cover - nur im Fehlerfall relevant
            fehler.append(exc)

    threads = [threading.Thread(target=gewinner_worker, args=(tag,)) for tag in range(10, 20)]
    threads.extend(threading.Thread(target=remove_worker) for _ in range(3))

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not fehler

    with sqlite3.connect(datenbank_pfad) as connection:
        gesamt = connection.execute("SELECT COUNT(*) FROM winner_entries").fetchone()[0]
        einzigartig = connection.execute(
            "SELECT COUNT(DISTINCT user_id || '-' || door) FROM winner_entries"
        ).fetchone()[0]

    assert gesamt == einzigartig
    advent.speichere_gewinner("77", "Testuser", 24, "Deterministischer Preis")
    assert advent.hat_gewonnen("77") is True

    advent.remove_user_from_winners_file("77")
    assert advent.hat_gewonnen("77") is False
