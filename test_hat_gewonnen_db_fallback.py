import sqlite3

import advent


def create_default_schema(connection):
    connection.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
        """
    )
    connection.execute(advent.USER_REWARDS_TABLE_SQL)


def test_hat_gewonnen_findet_direkten_datenbankgewinn(tmp_path, monkeypatch):
    database_path = tmp_path / "users.db"
    winners_file = tmp_path / "gewinner.txt"
    mapping_file = tmp_path / "gewinner_user_mapping.json"
    winners_file.write_text("", encoding="utf-8")
    mapping_file.write_text("[]", encoding="utf-8")

    monkeypatch.setattr(advent, "USER_DATABASE", str(database_path))
    monkeypatch.setattr(advent, "WINNERS_FILE", str(winners_file))
    monkeypatch.setattr(advent, "WINNER_USER_MAPPING_FILE", str(mapping_file))

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        create_default_schema(connection)
        connection.execute(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
            (7, "test7@example.com", "Test 7", ""),
        )
        connection.execute(
            """
            INSERT INTO user_rewards (
                user_id, year, door, prize_name, sponsor, sponsor_link, qr_filename, qr_content, created_at
            ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, datetime('now'))
            """,
            (7, 2025, 3, "Preis"),
        )

    assert advent.hat_gewonnen(7, 2025) is True


def test_hat_gewonnen_fallback_mit_mapping_auf_echte_user_id(tmp_path, monkeypatch):
    database_path = tmp_path / "users.db"
    winners_file = tmp_path / "gewinner.txt"
    mapping_file = tmp_path / "gewinner_user_mapping.json"

    winners_file.write_text(
        "99:Legacy User - Tag 5 - Nostalgiepreis - OV L11 - 2025\n",
        encoding="utf-8",
    )
    mapping_file.write_text(
        '[{"winner_id": 99, "user_id": 1, "display_name": "Real User"}]',
        encoding="utf-8",
    )

    monkeypatch.setattr(advent, "USER_DATABASE", str(database_path))
    monkeypatch.setattr(advent, "WINNERS_FILE", str(winners_file))
    monkeypatch.setattr(advent, "WINNER_USER_MAPPING_FILE", str(mapping_file))

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        create_default_schema(connection)
        connection.execute(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
            (1, "real@example.com", "Real User", ""),
        )

    assert advent.hat_gewonnen(1, 2025) is True
