"""hms_events — event publishing behind a single interface.

CLAUDE.md §2: "Never call a specific broker directly — publish through the
interface." Today the transport is Postgres/Redis; Kafka arrives later. Nothing
in the app should ever import a broker client directly.

Sprint-1 ships the interface plus a `NoopPublisher` for dev/tests so calling
code compiles and runs end-to-end. The transactional-outbox implementation
(`OutboxPublisher`) lands with the first real cross-service event.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from typing import Any, Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    topic: str            # e.g. "patient.registered", "referral.completed"
    key: str              # partition key — typically a resource id
    payload: dict[str, Any]
    tenant_id: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class Publisher(Protocol):
    """The single contract for emitting a platform event.

    Implementations MUST be safe to call inside a DB transaction — the reference
    implementation (transactional outbox) requires it, and callers should not
    have to know which implementation is wired up.
    """

    async def publish(self, event: Event) -> None: ...


class NoopPublisher:
    """Dev/test default: logs the event and drops it. Never wire this into prod."""

    async def publish(self, event: Event) -> None:
        log.info(
            "event(dev,dropped) topic=%s tenant=%s key=%s",
            event.topic, event.tenant_id, event.key,
        )


# The active publisher is resolved by the service composition root (main.py),
# not imported globally, so tests can inject a fake.
_publisher: Publisher = NoopPublisher()


def set_publisher(publisher: Publisher) -> None:
    """Set the active publisher implementation."""
    global _publisher
    _publisher = publisher


async def publish(
    topic: str,
    payload: dict[str, Any],
    key: str = "",
    tenant_id: str = "",
) -> None:
    """Publish an event through the active publisher interface.
    
    Validates that a non-empty tenant_id is resolved to enforce PLT-002.
    """
    # 1. Resolve tenant_id: check parameter -> payload -> context var
    resolved_tenant_id = tenant_id or payload.get("tenant_id")
    if not resolved_tenant_id:
        from hms_tenancy import current_tenant_id
        resolved_tenant_id = current_tenant_id.get()

    if not resolved_tenant_id:
        raise ValueError(
            f"Could not resolve tenant_id for topic '{topic}'. "
            "A non-empty tenant_id is required for multi-tenant isolation."
        )

    # 2. Resolve partition key: check parameter -> payload ID -> payload patient ID -> default empty
    resolved_key = key or payload.get("id") or payload.get("patient_id") or ""

    event = Event(
        topic=topic,
        key=str(resolved_key),
        payload=payload,
        tenant_id=resolved_tenant_id,
    )
    await _publisher.publish(event)

