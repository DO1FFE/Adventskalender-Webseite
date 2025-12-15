import sqlite3

import advent


def test_user_rewards_migration_and_recording(tmp_path, monkeypatch):
    database_path = tmp_path / "users.db"
    monkeypatch.setattr(advent, "USER_DATABASE", str(database_path))

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT,
                display_name TEXT,
                password_hash TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (1, 'test@example.com', 'Test User', '')"
        )
        connection.execute(
            """
            CREATE TABLE user_rewards (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                door INTEGER NOT NULL,
                prize_name TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO user_rewards (user_id, door, prize_name) VALUES (1, 1, 'Alter Preis')"
        )

    advent.init_user_db()

    recorded = advent.record_user_reward(
        1,
        2,
        "Neuer Preis",
        sponsor="Sponsor",
        sponsor_link="https://example.com",
        qr_filename="code.png",
        qr_content="qrdata",
    )

    assert recorded is True

    rewards = advent.get_user_rewards(1)
    rewards_by_door = {reward.get("door"): reward for reward in rewards}

    assert rewards_by_door[1]["prize_name"] == "Alter Preis"
    assert rewards_by_door[1]["created_at"]

    latest_reward = rewards_by_door[2]
    assert latest_reward["prize_name"] == "Neuer Preis"
    assert latest_reward["sponsor"] == "Sponsor"
    assert latest_reward["sponsor_link"] == "https://example.com"

