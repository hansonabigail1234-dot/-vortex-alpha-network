from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class UserProfile:
    telegram_user_id: int
    language: str | None = None
    country: str | None = None
    interests: tuple[str, ...] = ()
    onboarding_complete: bool = False
    public_channel_joined: bool = False
    alerts_enabled: bool = True


class UserProfileStore:
    """Durable onboarding and preference storage for Telegram users."""

    def __init__(self, database_path: str) -> None:
        self._lock = threading.RLock()
        database_parent = Path(database_path).expanduser().parent
        database_parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            database_path, check_same_thread=False, timeout=10
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                telegram_user_id INTEGER PRIMARY KEY,
                language TEXT,
                country TEXT,
                interests TEXT NOT NULL DEFAULT '[]',
                onboarding_complete INTEGER NOT NULL DEFAULT 0,
                public_channel_joined INTEGER NOT NULL DEFAULT 0,
                alerts_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def get(self, telegram_user_id: int) -> UserProfile:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT telegram_user_id, language, country, interests,
                       onboarding_complete, public_channel_joined, alerts_enabled
                FROM user_profiles
                WHERE telegram_user_id = ?
                """,
                (telegram_user_id,),
            ).fetchone()
        if row is None:
            return UserProfile(telegram_user_id=telegram_user_id)
        try:
            decoded_interests = json.loads(row["interests"])
            interests = tuple(
                item for item in decoded_interests if isinstance(item, str)
            )
        except (TypeError, json.JSONDecodeError):
            interests = ()
        return UserProfile(
            telegram_user_id=row["telegram_user_id"],
            language=row["language"],
            country=row["country"],
            interests=interests,
            onboarding_complete=bool(row["onboarding_complete"]),
            public_channel_joined=bool(row["public_channel_joined"]),
            alerts_enabled=bool(row["alerts_enabled"]),
        )

    def save(self, profile: UserProfile) -> UserProfile:
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO user_profiles
                    (telegram_user_id, language, country, interests,
                     onboarding_complete, public_channel_joined,
                     alerts_enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    language = excluded.language,
                    country = excluded.country,
                    interests = excluded.interests,
                    onboarding_complete = excluded.onboarding_complete,
                    public_channel_joined = excluded.public_channel_joined,
                    alerts_enabled = excluded.alerts_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.telegram_user_id,
                    profile.language,
                    profile.country,
                    json.dumps(list(profile.interests)),
                    int(profile.onboarding_complete),
                    int(profile.public_channel_joined),
                    int(profile.alerts_enabled),
                    updated_at,
                ),
            )
            self._connection.commit()
        return profile

    def update(
        self,
        telegram_user_id: int,
        *,
        language: str | None = None,
        country: str | None = None,
        interests: tuple[str, ...] | None = None,
        onboarding_complete: bool | None = None,
        public_channel_joined: bool | None = None,
        alerts_enabled: bool | None = None,
    ) -> UserProfile:
        current = self.get(telegram_user_id)
        return self.save(
            UserProfile(
                telegram_user_id=telegram_user_id,
                language=current.language if language is None else language,
                country=current.country if country is None else country,
                interests=current.interests if interests is None else interests,
                onboarding_complete=(
                    current.onboarding_complete
                    if onboarding_complete is None
                    else onboarding_complete
                ),
                public_channel_joined=(
                    current.public_channel_joined
                    if public_channel_joined is None
                    else public_channel_joined
                ),
                alerts_enabled=(
                    current.alerts_enabled
                    if alerts_enabled is None
                    else alerts_enabled
                ),
            )
        )