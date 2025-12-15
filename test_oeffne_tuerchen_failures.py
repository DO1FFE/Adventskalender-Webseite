import datetime
import sqlite3

import pytz

import advent


def _prepare_winning_request(tmp_path, monkeypatch):
    database_path = tmp_path / "users.db"
    monkeypatch.setattr(advent, "USER_DATABASE", str(database_path))
    advent.init_user_db()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (1, 'test@example.com', 'Tester', '')"
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

    return client


def test_user_reward_failure_returns_error_page(monkeypatch, tmp_path):
    client = _prepare_winning_request(tmp_path, monkeypatch)
    monkeypatch.setattr(advent, "record_user_reward", lambda *_, **__: False)

    response = client.get("/oeffne_tuerchen/5")
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "Fehler" in body
    assert "Gl\u00fcckwunsch" not in body


def test_user_reward_exception_returns_error_page(monkeypatch, tmp_path):
    client = _prepare_winning_request(tmp_path, monkeypatch)

    def _raise_error(*_, **__):
        raise sqlite3.DatabaseError("boom")

    monkeypatch.setattr(advent, "record_user_reward", _raise_error)

    response = client.get("/oeffne_tuerchen/5")
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "Fehler" in body
    assert "Gl\u00fcckwunsch" not in body
