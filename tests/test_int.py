from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import app
from app.routers.integration import generate_webhook_signature

client = TestClient(app)


@pytest.fixture
def db_session():
    mock = AsyncMock(spec=AsyncSession)
    return mock


@pytest.fixture(autouse=True)
def override_db(db_session):
    async def override_get_session():
        yield db_session
    app.dependency_overrides[get_session] = override_get_session
    yield
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture(autouse=True)
def _provision_test_tenants():
    """Override database provision fixtures to do nothing."""
    pass


def test_create_webhook_subscription(db_session):
    """Test webhook subscription creation."""
    mock_insert = MagicMock()
    mock_insert.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "event_type": "invoice.finalized",
        "url": "https://callback.external.com/webhooks",
        "secret_key": "supersecretkey",
        "active": True
    }

    # Executes: SET LOCAL, INSERT subscription
    db_session.execute.side_effect = [AsyncMock(), mock_insert]

    response = client.post(
        "/int/webhooks/subscriptions",
        headers={"Authorization": "Bearer dev.apollo.admin"},
        json={
            "event_type": "invoice.finalized",
            "url": "https://callback.external.com/webhooks",
            "secret_key": "supersecretkey"
        }
    )

    assert response.status_code == 201
    assert response.json()["secret_key"] == "supersecretkey"


def test_generate_webhook_signature():
    """Test HMAC SHA256 signature algorithm correctly signs payloads (INT-006)."""
    payload = '{"event":"invoice.finalized","amount":525.0}'
    secret = "my_shared_secret"
    timestamp = "1774051200"  # Target timestamp

    sig = generate_webhook_signature(payload, secret, timestamp)
    
    # Re-calculate independently to verify
    import hmac
    import hashlib
    expected_msg = f"{timestamp}.{payload}".encode("utf-8")
    expected_sig = hmac.new(secret.encode("utf-8"), expected_msg, hashlib.sha256).hexdigest()
    
    assert sig == expected_sig


def test_ingest_inbound_hl7_oru_success(db_session):
    """Test parser translates inbound HL7 ORU result messages successfully (INT-002)."""
    hl7_text = (
        "MSH|^~\\&|LIS|FACILITY|HMS_PLT|FACILITY|202607202300||ORU^R01|MSG0002|P|2.3\n"
        "PID|1||PID9876||LastName^FirstName\n"
        "OBR|1|OBR123||883-9^Hemoglobin^LN||||||||||||||\n"
        "OBX|1|NM|883-9^Hemoglobin^LN||14.5|g/dL|12-16|N|||F"
    )

    mock_log_insert = MagicMock()
    mock_log_insert.mappings.return_value.one.return_value = {
        "id": "55555555-6666-7777-8888-999999999999"
    }

    # Executes: SET LOCAL, INSERT integration_log
    db_session.execute.side_effect = [AsyncMock(), mock_log_insert]

    response = client.post(
        "/int/hl7/inbound",
        headers={"Authorization": "Bearer dev.apollo.admin"},
        json={"message_text": hl7_text}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "MSH" in data["parsed_segments"]
    assert "PID" in data["parsed_segments"]
    assert "OBX" in data["parsed_segments"]


def test_generate_outbound_hl7_orm(db_session):
    """Test formatting outbound laboratory order triggers ORM segments mapping (INT-001)."""
    mock_order_select = MagicMock()
    mock_order_select.mappings.return_value.one_or_none.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "family_name": "Kumar",
        "given_name": "Siva"
    }

    # Executes: SET LOCAL, SELECT order, INSERT integration_log
    db_session.execute.side_effect = [AsyncMock(), mock_order_select, AsyncMock()]

    response = client.post(
        "/int/hl7/outbound/order?order_id=11111111-2222-3333-4444-555555555555",
        headers={"Authorization": "Bearer dev.apollo.admin"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "MSH|^~\\&|HMS_PLT" in data["hl7_message"]
    assert "PID|1||22222222-3333-4444-5555-666666666666||Kumar^Siva" in data["hl7_message"]


def test_mock_payment_gateway_charge(db_session):
    """Test payment gateway reference implementation captures charge successfully."""
    response = client.post(
        "/int/payments/gateway/charge",
        headers={"Authorization": "Bearer dev.apollo.billing_clerk"},
        json={
            "amount": 525.0,
            "payment_method_id": "pm_tok_visa_999"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "captured"
    assert data["amount"] == 525.0
    assert data["transaction_reference"].startswith("txn_")
