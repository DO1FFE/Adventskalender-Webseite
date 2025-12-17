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


def test_import_prefers_existing_user_by_display_name(tmp_path, monkeypatch):
    database_path = tmp_path / "users.db"
    winners_file = tmp_path / "gewinner.txt"

    winners_file.write_text("99: Test User - Tag 1 - Preis\n", encoding="utf-8")

    monkeypatch.setattr(advent, "USER_DATABASE", str(database_path))
    monkeypatch.setattr(advent, "WINNERS_FILE", str(winners_file))

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        create_default_schema(connection)
        connection.execute(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
            (2, "test@example.com", "Test User", ""),
        )

        imported = advent.import_rewards_from_winners_file(connection, str(winners_file))

        cursor = connection.execute("SELECT user_id, door FROM user_rewards")
        stored_entries = [tuple(row) for row in cursor.fetchall()]

    assert imported == 1
    assert stored_entries == [(2, 1)]


def test_placeholder_rewards_migration(tmp_path):
    database_path = tmp_path / "users.db"
    mapping_file = tmp_path / "gewinner_user_mapping.json"

    mapping_file.write_text(
        '[{"winner_id": 99, "user_id": 1, "display_name": "Real User"}]',
        encoding="utf-8",
    )

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        create_default_schema(connection)
        connection.execute(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
            (1, "real@example.com", "Real User", ""),
        )
        connection.execute(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
            (99, "user-99@example.invalid", "Real User", ""),
        )
        connection.execute(
            """
            INSERT INTO user_rewards (
                user_id, door, prize_name, sponsor, sponsor_link, qr_filename, qr_content, created_at
            ) VALUES (?, ?, ?, NULL, NULL, NULL, NULL, datetime('now'))
            """,
            (99, 1, "Preis"),
        )

        migrated, removed = advent.migrate_placeholder_user_rewards(connection, str(mapping_file))

        migrated_entries = [
            tuple(row)
            for row in connection.execute(
                "SELECT user_id, door FROM user_rewards ORDER BY id"
            ).fetchall()
        ]
        remaining_users = [
            tuple(row)
            for row in connection.execute("SELECT id FROM users ORDER BY id").fetchall()
        ]

    assert migrated == 1
    assert removed == 1
    assert migrated_entries == [(1, 1)]
    assert remaining_users == [(1,)]
