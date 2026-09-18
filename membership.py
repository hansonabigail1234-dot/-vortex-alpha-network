from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping

LOGGER = logging.getLogger("vortex_alpha_network.membership")

ActivationNotifier = Callable[[int, str], None]

PRODUCTS = {
    "crypto": {
        "name": "Crypto Alpha",
        "price": "$10/month",
        "tribute_link": "CRYPTO_TRIBUTE_LINK",
        "private_link": "CRYPTO_PRIVATE_CHANNEL_LINK",
        "product_id": "TRIBUTE_CRYPTO_PRODUCT_ID",
    },
    "jobs": {
        "name": "Remote Jobs",
        "price": "$5/month",
        "tribute_link": "JOBS_TRIBUTE_LINK",
        "private_link": "JOBS_PRIVATE_CHANNEL_LINK",
        "product_id": "TRIBUTE_JOBS_PRODUCT_ID",
    },
    "sport": {
        "name": "Sport Edge",
        "price": "$5/month",
        "tribute_link": "SPORT_TRIBUTE_LINK",
        "private_link": "SPORT_PRIVATE_CHANNEL_LINK",
        "product_id": "TRIBUTE_SPORT_PRODUCT_ID",
    },
}

ACTIVE_STATUSES = frozenset({"active", "paid", "succeeded", "completed", "trialing"})
INACTIVE_STATUSES = frozenset(
    {
        "pending",
        "awaiting_confirmation",
        "failed",
        "canceled",
        "cancelled",
        "expired",
        "refunded",
        "past_due",
        "unpaid",
        "inactive",
    }
)


@dataclass(frozen=True)
class MembershipConfig:
    public_channel_link: str | None
    tribute_links: dict[str, str | None]
    private_channel_links: dict[str, str | None]
    tribute_product_ids: dict[str, str | None]
    webhook_secret: str | None
    webhook_path: str
    webhook_port: int
    database_path: str

    @classmethod
    def from_env(cls) -> "MembershipConfig":
        try:
            port = int(os.getenv("TRIBUTE_WEBHOOK_PORT", os.getenv("PORT", "8080")))
        except ValueError as exc:
            raise RuntimeError("TRIBUTE_WEBHOOK_PORT must be a valid port number.") from exc
        if not 1 <= port <= 65535:
            raise RuntimeError("TRIBUTE_WEBHOOK_PORT must be between 1 and 65535.")

        return cls(
            public_channel_link=os.getenv("PUBLIC_CHANNEL_LINK"),
            tribute_links={
                product: os.getenv(values["tribute_link"])
                for product, values in PRODUCTS.items()
            },
            private_channel_links={
                product: os.getenv(values["private_link"])
                for product, values in PRODUCTS.items()
            },
            tribute_product_ids={
                product: os.getenv(values["product_id"])
                for product, values in PRODUCTS.items()
            },
            webhook_secret=os.getenv("TRIBUTE_WEBHOOK_SECRET"),
            webhook_path=os.getenv("TRIBUTE_WEBHOOK_PATH", "/tribute/webhook"),
            webhook_port=port,
            database_path=os.getenv(
                "MEMBERSHIP_DB_PATH", "vortex_alpha_memberships.sqlite3"
            ),
        )


class MembershipStore:
    """Small durable store for one bot process and its membership records."""

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
            CREATE TABLE IF NOT EXISTS memberships (
                telegram_user_id INTEGER NOT NULL,
                product TEXT NOT NULL,
                status TEXT NOT NULL,
                active_until TEXT,
                updated_at TEXT NOT NULL,
                event_id TEXT,
                PRIMARY KEY (telegram_user_id, product)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def set_subscription(
        self,
        *,
        telegram_user_id: int,
        product: str,
        status: str,
        active_until: str | None,
        event_id: str | None,
    ) -> bool:
        if product not in PRODUCTS:
            raise ValueError("Unsupported membership product.")
        if status not in ACTIVE_STATUSES | INACTIVE_STATUSES:
            raise ValueError("Unsupported membership status.")

        with self._lock:
            if event_id:
                existing_event = self._connection.execute(
                    "SELECT 1 FROM processed_events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if existing_event:
                    return False

            updated_at = datetime.now(timezone.utc).isoformat()
            self._connection.execute(
                """
                INSERT INTO memberships
                    (telegram_user_id, product, status, active_until, updated_at, event_id)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id, product) DO UPDATE SET
                    status = excluded.status,
                    active_until = excluded.active_until,
                    updated_at = excluded.updated_at,
                    event_id = excluded.event_id
                """,
                (
                    telegram_user_id,
                    product,
                    status,
                    active_until,
                    updated_at,
                    event_id,
                ),
            )
            if event_id:
                self._connection.execute(
                    "INSERT INTO processed_events (event_id, processed_at) VALUES (?, ?)",
                    (event_id, updated_at),
                )
            self._connection.commit()
            return True

    def status_for(self, telegram_user_id: int, product: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT status, active_until
                FROM memberships
                WHERE telegram_user_id = ? AND product = ?
                """,
                (telegram_user_id, product),
            ).fetchone()
        if not row:
            return None
        if row["status"] in ACTIVE_STATUSES and _is_expired(row["active_until"]):
            return "expired"
        return row["status"]

    def is_active(self, telegram_user_id: int, product: str) -> bool:
        return self.status_for(telegram_user_id, product) in ACTIVE_STATUSES


def _is_expired(value: str | None) -> bool:
    if not value:
        return False
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        LOGGER.warning("Ignoring unparseable membership expiry.")
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= datetime.now(timezone.utc)


def _nested_values(payload: Mapping[str, Any], *paths: tuple[str, ...]) -> list[Any]:
    values: list[Any] = []
    for path in paths:
        value: Any = payload
        for key in path:
            if not isinstance(value, Mapping):
                value = None
                break
            value = value.get(key)
        if value is not None:
            values.append(value)
    return values


def _first_string(values: list[Any]) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return None


def _telegram_user_id(payload: Mapping[str, Any]) -> int:
    raw = _first_string(
        _nested_values(
            payload,
            ("telegram_user_id",),
            ("telegram_id",),
            ("user_id",),
            ("user", "telegram_user_id"),
            ("customer", "telegram_user_id"),
            ("metadata", "telegram_user_id"),
        )
    )
    if raw is None:
        raise ValueError("Tribute event is missing a Telegram user ID.")
    try:
        user_id = int(raw)
    except ValueError as exc:
        raise ValueError("Tribute Telegram user ID must be an integer.") from exc
    if user_id <= 0:
        raise ValueError("Tribute Telegram user ID must be positive.")
    return user_id


def _product(payload: Mapping[str, Any], config: MembershipConfig) -> str:
    raw = _first_string(
        _nested_values(
            payload,
            ("product",),
            ("product_id",),
            ("plan",),
            ("plan_id",),
            ("membership",),
            ("subscription", "product"),
            ("subscription", "product_id"),
            ("metadata", "product"),
        )
    )
    if raw is None:
        raise ValueError("Tribute event is missing a product.")

    normalized = raw.casefold().replace("-", "_").replace(" ", "_")
    for product, values in PRODUCTS.items():
        configured_id = config.tribute_product_ids[product]
        aliases = {
            product,
            f"{product}_alpha" if product == "crypto" else product,
            f"{product}_edge" if product == "sport" else product,
            "remote_jobs" if product == "jobs" else product,
            f"vortex_{product}",
            f"vortex_{product}_alpha" if product == "crypto" else f"vortex_{product}",
            "vortex_remote_jobs" if product == "jobs" else f"vortex_{product}_edge",
        }
        if configured_id:
            aliases.add(
                configured_id.casefold().replace("-", "_").replace(" ", "_")
            )
        if normalized in aliases:
            return product
    raise ValueError("Tribute event contains an unsupported product.")


def _status(payload: Mapping[str, Any]) -> str:
    raw = _first_string(
        _nested_values(
            payload,
            ("status",),
            ("event", "status"),
            ("subscription", "status"),
            ("payment", "status"),
        )
    )
    if raw is None:
        raise ValueError("Tribute event is missing a payment status.")
    normalized = raw.casefold().replace("-", "_").replace(" ", "_")
    if normalized not in ACTIVE_STATUSES | INACTIVE_STATUSES:
        raise ValueError("Tribute event contains an unsupported payment status.")
    return normalized


def _active_until(payload: Mapping[str, Any]) -> str | None:
    raw = _first_string(
        _nested_values(
            payload,
            ("active_until",),
            ("expires_at",),
            ("valid_until",),
            ("current_period_end",),
            ("subscription", "expires_at"),
            ("subscription", "current_period_end"),
        )
    )
    if raw is None:
        return None
    if raw.isdigit():
        timestamp = int(raw)
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Tribute membership expiry must be an ISO date or timestamp.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _event_id(payload: Mapping[str, Any]) -> str | None:
    return _first_string(
        _nested_values(
            payload,
            ("event_id",),
            ("id",),
            ("event", "id"),
            ("webhook_id",),
        )
    )


def verify_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not signature:
        return False
    provided = signature.strip()
    if provided.startswith("sha256="):
        provided = provided.removeprefix("sha256=")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def process_tribute_event(
    raw_body: bytes,
    *,
    signature: str | None,
    config: MembershipConfig,
    store: MembershipStore,
    on_activation: ActivationNotifier | None = None,
) -> bool:
    if not config.webhook_secret or not verify_signature(
        raw_body, signature, config.webhook_secret
    ):
        raise PermissionError("Invalid Tribute webhook signature.")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError("Tribute webhook body must be valid JSON.") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Tribute webhook body must be a JSON object.")

    telegram_user_id = _telegram_user_id(payload)
    product = _product(payload, config)
    status = _status(payload)
    processed = store.set_subscription(
        telegram_user_id=telegram_user_id,
        product=product,
        status=status,
        active_until=_active_until(payload),
        event_id=_event_id(payload),
    )
    if (
        processed
        and status in ACTIVE_STATUSES
        and store.is_active(telegram_user_id, product)
        and on_activation
    ):
        try:
            on_activation(telegram_user_id, product)
        except Exception:
            LOGGER.exception(
                "Unable to schedule %s activation notification for Telegram user %s.",
                product,
                telegram_user_id,
            )
    return processed


def _signature_from_headers(headers: Mapping[str, str]) -> str | None:
    return (
        headers.get("X-Tribute-Signature")
        or headers.get("X-Webhook-Signature")
        or headers.get("X-Signature")
    )


class _TributeWebhookHandler(BaseHTTPRequestHandler):
    server: "_TributeWebhookHTTPServer"

    def do_POST(self) -> None:
        if self.path != self.server.webhook_path:
            self._respond(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._respond(HTTPStatus.BAD_REQUEST, {"error": "Invalid content length."})
            return
        if content_length <= 0 or content_length > 1_000_000:
            self._respond(HTTPStatus.BAD_REQUEST, {"error": "Invalid request body."})
            return

        raw_body = self.rfile.read(content_length)
        try:
            processed = process_tribute_event(
                raw_body,
                signature=_signature_from_headers(self.headers),
                config=self.server.config,
                store=self.server.store,
                on_activation=self.server.on_activation,
            )
        except PermissionError:
            LOGGER.warning("Rejected Tribute webhook with an invalid signature.")
            self._respond(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized."})
        except ValueError as exc:
            LOGGER.warning("Rejected invalid Tribute webhook: %s", exc)
            self._respond(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:
            LOGGER.exception("Unexpected Tribute webhook processing failure.")
            self._respond(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "Unable to process webhook."},
            )
        else:
            self._respond(HTTPStatus.OK, {"processed": processed})

    def do_GET(self) -> None:
        if self.path == "/health":
            self._respond(HTTPStatus.OK, {"status": "ok"})
            return
        self._respond(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("Tribute webhook: " + format, *args)

    def _respond(self, status: HTTPStatus, body: Mapping[str, Any]) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class _TributeWebhookHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        *,
        config: MembershipConfig,
        store: MembershipStore,
        on_activation: ActivationNotifier | None = None,
    ) -> None:
        super().__init__(address, _TributeWebhookHandler)
        self.config = config
        self.store = store
        self.on_activation = on_activation
        self.webhook_path = config.webhook_path


def start_tribute_webhook(
    *,
    config: MembershipConfig,
    store: MembershipStore,
    on_activation: ActivationNotifier | None = None,
) -> ThreadingHTTPServer | None:
    if not config.webhook_secret:
        LOGGER.warning(
            "TRIBUTE_WEBHOOK_SECRET is not configured; payment verification is disabled."
        )
        return None
    server = _TributeWebhookHTTPServer(
        ("0.0.0.0", config.webhook_port),
        config=config,
        store=store,
        on_activation=on_activation,
    )
    thread = threading.Thread(
        target=server.serve_forever,
        name="tribute-webhook",
        daemon=True,
    )
    thread.start()
    LOGGER.info(
        "Tribute webhook listening on port %s at %s",
        config.webhook_port,
        config.webhook_path,
    )
    return server