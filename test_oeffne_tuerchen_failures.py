import datetime
import json
import os
import sqlite3

import pytz

import advent


def _prepare_winning_request(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    database_path = tmp_path / "users.db"
    winners_path = tmp_path / "gewinner.txt"
    prizes_path = tmp_path / "preise.json"
    daily_counter_path = tmp_path / "tagespreise.json"

    monkeypatch.setattr(advent, "USER_DATABASE", str(database_path))
    monkeypatch.setattr(advent, "WINNERS_FILE", str(winners_path))
    monkeypatch.setattr(advent, "PRIZE_FILE", str(prizes_path))
    monkeypatch.setattr(advent, "DAILY_PRIZE_FILE", str(daily_counter_path))
    advent.init_user_db()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (1, 'test@example.com', 'Tester', '')"
        )

    with open(prizes_path, "w", encoding="utf-8") as datei:
        json.dump(
            [
                {
                    "name": "Testpreis",
                    "total": 1,
                    "remaining": 1,
                    "sponsor": "Sponsor",
                    "sponsor_link": "https://example.com",
                }
            ],
            datei,
            ensure_ascii=False,
            indent=2,
        )

    fake_now = datetime.datetime(2023, 12, 5, 12, 0, tzinfo=pytz.utc)
    monkeypatch.setattr(advent, "get_local_datetime", lambda: fake_now)
    monkeypatch.setattr(advent, "get_calendar_active", lambda: True)
    monkeypatch.setattr(advent, "hat_teilgenommen", lambda *_, **__: False)
    monkeypatch.setattr(advent, "speichere_teilnehmer", lambda *_, **__: None)
    originale_get_prize_stats = advent.get_prize_stats
    monkeypatch.setattr(advent, "get_prize_stats", lambda: originale_get_prize_stats(advent.load_prizes()))
    monkeypatch.setattr(advent, "verbleibende_tage_bis_letztes_tuerchen", lambda _date: 1)
    monkeypatch.setattr(advent, "gewinnchance_ermitteln", lambda *_, **__: 1.0)
    monkeypatch.setattr(advent, "waehle_preis_ohne_persistenz", lambda prizes, _day: (0, prizes[0]))
    monkeypatch.setattr(advent.random, "random", lambda: 0.0)

    advent.tuerchen_status.clear()
    advent.tuerchen_status.update({tag: set() for tag in range(1, 25)})

    client = advent.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1

    return client, prizes_path, daily_counter_path, winners_path


def _load_prize_remaining(prizes_path):
    with open(prizes_path, "r", encoding="utf-8") as datei:
        data = json.load(datei)
    return data[0]["remaining"]


def _reward_count(database_path):
    with sqlite3.connect(database_path) as verbindung:
        return verbindung.execute("SELECT COUNT(*) FROM user_rewards").fetchone()[0]


def _daily_counter(daily_counter_path, date_key):
    if not daily_counter_path.exists():
        return 0
    with open(daily_counter_path, "r", encoding="utf-8") as datei:
        data = json.load(datei)
    return data.get("awards", {}).get(date_key, 0)


def test_user_reward_failure_returns_error_page(monkeypatch, tmp_path):
    client, prizes_path, daily_counter_path, winners_path = _prepare_winning_request(tmp_path, monkeypatch)
    database_path = tmp_path / "users.db"
    today_key = "2023-12-05"
    monkeypatch.setattr(advent, "record_user_reward", lambda *_, **__: False)

    response = client.get("/oeffne_tuerchen/5")
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "Fehler" in body
    assert "Gl\u00fcckwunsch" not in body
    assert _load_prize_remaining(prizes_path) == 1
    assert _daily_counter(daily_counter_path, today_key) == 0
    assert not winners_path.exists()
    assert _reward_count(database_path) == 0


def test_user_reward_exception_returns_error_page(monkeypatch, tmp_path):
    client, prizes_path, daily_counter_path, winners_path = _prepare_winning_request(tmp_path, monkeypatch)
    database_path = tmp_path / "users.db"
    today_key = "2023-12-05"

    def _raise_error(*_, **__):
        raise sqlite3.DatabaseError("boom")

    monkeypatch.setattr(advent, "record_user_reward", _raise_error)

    response = client.get("/oeffne_tuerchen/5")
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "Fehler" in body
    assert "Gl\u00fcckwunsch" not in body
    assert _load_prize_remaining(prizes_path) == 1
    assert _daily_counter(daily_counter_path, today_key) == 0
    assert not winners_path.exists()
    assert _reward_count(database_path) == 0


def test_qr_error_rolls_back_without_persistent_changes(monkeypatch, tmp_path):
    client, prizes_path, daily_counter_path, winners_path = _prepare_winning_request(tmp_path, monkeypatch)
    database_path = tmp_path / "users.db"
    today_key = "2023-12-05"

    class FehlerhafteQRCode:
        def __init__(self, *_, **__):
            raise RuntimeError("kein qr")

    monkeypatch.setattr(advent.qrcode, "QRCode", FehlerhafteQRCode)

    response = client.get("/oeffne_tuerchen/5")
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "Fehler" in body
    assert _load_prize_remaining(prizes_path) == 1
    assert _daily_counter(daily_counter_path, today_key) == 0
    assert not winners_path.exists()
    assert not os.path.exists("qr_codes/user_1_5.png")
    assert _reward_count(database_path) == 0
