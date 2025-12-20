import datetime
import sqlite3

import pytz

import advent


def test_oeffne_tuerchen_migrates_user_rewards_index(tmp_path, monkeypatch):
    database_path = tmp_path / "users.db"
    monkeypatch.setattr(advent, "USER_DATABASE", str(database_path))

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT UNIQUE NOT NULL CHECK (trim(email) <> ''),
                display_name TEXT NOT NULL CHECK (trim(display_name) <> ''),
                password_hash TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (1, 'test@example.com', 'Tester', '')"
        )
        connection.execute(
            """
            CREATE TABLE user_rewards (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                door INTEGER NOT NULL,
                prize_name TEXT NOT NULL,
                sponsor TEXT,
                sponsor_link TEXT,
                qr_filename TEXT,
                qr_content TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )

    fake_now = datetime.datetime(2023, 12, 5, 12, 0, tzinfo=pytz.utc)
    monkeypatch.setattr(advent, "get_local_datetime", lambda: fake_now)
    monkeypatch.setattr(advent, "get_calendar_active", lambda: True)
    monkeypatch.setattr(advent, "hat_teilgenommen", lambda *_, **__: False)
    monkeypatch.setattr(advent, "speichere_teilnehmer", lambda *_, **__: None)
    monkeypatch.setattr(
        advent,
        "get_prize_stats",
        lambda: (
            [
                {
                    "name": "Testpreis",
                    "remaining": 1,
                    "sponsor": "Sponsor",
                    "sponsor_link": "https://example.com",
                }
            ],
            1,
            1,
            None,
        ),
    )
    monkeypatch.setattr(advent, "verbleibende_tage_bis_letztes_tuerchen", lambda _date: 1)
    monkeypatch.setattr(advent, "increment_daily_awarded_prizes", lambda *_, **__: None)
    monkeypatch.setattr(advent, "gewinnchance_ermitteln", lambda *_, **__: 1.0)
    monkeypatch.setattr(advent, "reduce_prize", lambda prizes, _day: prizes[0])
    monkeypatch.setattr(advent.random, "random", lambda: 0.0)

    advent.tuerchen_status.clear()
    advent.tuerchen_status.update({tag: set() for tag in range(1, 25)})

    client = advent.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1

    advent.init_user_db()

    response = client.get("/oeffne_tuerchen/5")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Gl\u00fcckwunsch" in body

    with sqlite3.connect(database_path) as connection:
        rewards = connection.execute(
            "SELECT user_id, door, prize_name FROM user_rewards WHERE door = 5"
        ).fetchall()

    assert rewards == [(1, 5, "Testpreis")]
