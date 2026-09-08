"""Revocable application sessions issued after a verified OIDC login."""


from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from hermes_cli.dashboard_auth import ProviderError, RefreshExpiredError, Session
from hermes_cli.sqlite_safe_read import connect_tracked
from hermes_cli.sqlite_util import write_txn

ACCESS_PREFIX = "hermes-oidc-at."
REFRESH_PREFIX = "hermes-oidc-rt."
ACCESS_TTL_SECONDS = 900


class OIDCSessionStore:
    def __init__(self, path: Path, *, issuer: str, client_id: str, ttl_seconds: int):
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.namespace = hashlib.sha256(
            json.dumps([issuer, client_id, ttl_seconds]).encode()
        ).hexdigest()

    @contextmanager
    def _connection(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.path.touch(mode=0o600, exist_ok=True)
            with closing(connect_tracked(self.path, timeout=5, isolation_level=None)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS oidc_sessions ("
                    "id TEXT PRIMARY KEY, namespace TEXT NOT NULL, "
                    "refresh_hash TEXT NOT NULL UNIQUE, identity TEXT NOT NULL, "
                    "created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS oidc_access_tokens ("
                    "token_hash TEXT PRIMARY KEY, session_id TEXT NOT NULL "
                    "REFERENCES oidc_sessions(id) ON DELETE CASCADE, "
                    "expires_at INTEGER NOT NULL)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS oidc_access_session ON oidc_access_tokens(session_id)"
                )
                yield connection
        except (OSError, sqlite3.Error) as exc:
            raise ProviderError("OIDC application session store unavailable") from exc

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _cleanup(connection, now: int) -> None:
        connection.execute("DELETE FROM oidc_access_tokens WHERE expires_at <= ?", (now,))
        connection.execute("DELETE FROM oidc_sessions WHERE expires_at <= ?", (now,))

    @staticmethod
    def _session(identity: str, access_token: str, refresh_token: str, expires_at: int) -> Session:
        fields = json.loads(identity)
        return Session(**fields, access_token=access_token, refresh_token=refresh_token, expires_at=expires_at)

    def _mint_access(self, connection, row, refresh_token: str, now: int) -> Session:
        access_token = ACCESS_PREFIX + secrets.token_urlsafe(32)
        expires_at = min(now + ACCESS_TTL_SECONDS, row["expires_at"])
        connection.execute(
            "INSERT INTO oidc_access_tokens (token_hash, session_id, expires_at) VALUES (?, ?, ?)",
            (self._digest(access_token), row["id"], expires_at),
        )
        return self._session(row["identity"], access_token, refresh_token, expires_at)

    def issue(self, identity: Session) -> Session:
        now = int(time.time())
        refresh_token = REFRESH_PREFIX + secrets.token_urlsafe(32)
        row = {
            "id": secrets.token_urlsafe(32),
            "identity": json.dumps({
                "user_id": identity.user_id,
                "email": identity.email,
                "display_name": identity.display_name,
                "org_id": identity.org_id,
                "provider": identity.provider,
            }),
            "expires_at": now + self.ttl_seconds,
        }
        with self._connection() as connection, write_txn(connection):
            self._cleanup(connection, now)
            connection.execute(
                "INSERT INTO oidc_sessions (id, namespace, refresh_hash, identity, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row["id"], self.namespace, self._digest(refresh_token), row["identity"], now, row["expires_at"]),
            )
            return self._mint_access(connection, row, refresh_token, now)

    def verify(self, access_token: str) -> Session | None:
        if not access_token.startswith(ACCESS_PREFIX) or not self.path.exists():
            return None
        now = int(time.time())
        with self._connection() as connection:
            row = connection.execute(
                "SELECT sessions.identity, tokens.expires_at FROM oidc_access_tokens tokens "
                "JOIN oidc_sessions sessions ON sessions.id=tokens.session_id "
                "WHERE tokens.token_hash=? AND sessions.namespace=? "
                "AND tokens.expires_at>? AND sessions.expires_at>?",
                (self._digest(access_token), self.namespace, now, now),
            ).fetchone()
        if row is None:
            return None
        return self._session(row["identity"], access_token, "", row["expires_at"])

    def refresh(self, refresh_token: str) -> Session:
        if not refresh_token.startswith(REFRESH_PREFIX) or not self.path.exists():
            raise RefreshExpiredError("Unknown OIDC application session")
        now = int(time.time())
        with self._connection() as connection, write_txn(connection):
            self._cleanup(connection, now)
            row = connection.execute(
                "SELECT id, identity, expires_at FROM oidc_sessions "
                "WHERE refresh_hash=? AND namespace=? AND expires_at>?",
                (self._digest(refresh_token), self.namespace, now),
            ).fetchone()
            if row is None:
                raise RefreshExpiredError("OIDC application session expired or revoked")
            return self._mint_access(connection, row, refresh_token, now)

    def revoke(self, refresh_token: str) -> None:
        if not refresh_token.startswith(REFRESH_PREFIX) or not self.path.exists():
            return
        with self._connection() as connection, write_txn(connection):
            connection.execute(
                "DELETE FROM oidc_sessions WHERE refresh_hash=? AND namespace=?",
                (self._digest(refresh_token), self.namespace),
            )
