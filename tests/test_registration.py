from __future__ import annotations

from datetime import date
import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import app
from app.routers.patients import check_duplicate_patient, map_to_fhir_patient
from hms_tenancy import RequestContext


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


client = TestClient(app)


@pytest.mark.asyncio
async def test_duplicate_detection_deterministic():
    """Test check_duplicate_patient finds exact duplicates by ABHA, Aadhaar, Aarogyasri or PMJAY."""
    mock_session = AsyncMock(spec=AsyncSession)
    
    # 1. Deterministic match Mock
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = [
        {"id": "uuid-1", "given_name": "Ravi", "family_name": "Kumar", "dob": date(1985, 5, 20), "phone": "9876543210"}
    ]
    mock_session.execute.return_value = mock_result

    from app.schemas import PatientCreate
    patient_data = PatientCreate(
        given_name="Ravi",
        family_name="Kumar",
        dob=date(1985, 5, 20),
        phone="9876543210",
        national_id="Aadhaar-1234"
    )

    dupes = await check_duplicate_patient(mock_session, "tenantA", patient_data)
    assert len(dupes) == 1
    assert dupes[0]["id"] == "uuid-1"
    assert dupes[0]["score"] == 1.0
    assert "Deterministic" in dupes[0]["match_reason"]


@pytest.mark.asyncio
async def test_duplicate_detection_probabilistic():
    """Test check_duplicate_patient scores probabilistic duplicates by phone, DOB, names, gender."""
    mock_session = AsyncMock(spec=AsyncSession)
    
    # First call: no deterministic match (empty list)
    # Second call: probabilistic match candidate
    mock_result_empty = MagicMock()
    mock_result_empty.mappings.return_value.all.return_value = []
    
    mock_result_prob = MagicMock()
    mock_result_prob.mappings.return_value.all.return_value = [
        # Match given_name (0.2), family_name (0.2), dob (0.3), phone (0.4) -> Score 1.1 -> >= 0.7 threshold
        {"id": "uuid-prob", "given_name": "Ravi", "family_name": "Kumar", "dob": date(1985, 5, 20), "phone": "9876543210", "gender": "male"}
    ]
    
    mock_session.execute.side_effect = [mock_result_empty, mock_result_prob]

    from app.schemas import PatientCreate
    patient_data = PatientCreate(
        given_name="Ravi",
        family_name="Kumar",
        dob=date(1985, 5, 20),
        phone="9876543210",
        gender="male",
        abha_number="12345678901234"  # Distinct deterministic ID to trigger query
    )

    dupes = await check_duplicate_patient(mock_session, "tenantA", patient_data)
    assert len(dupes) == 1
    assert dupes[0]["id"] == "uuid-prob"
    assert dupes[0]["score"] >= 0.7


def test_fhir_mapping_and_validation():
    """Test mapping and validation helper converts demographics to clean FHIR R4 Patient representation."""
    from app.schemas import PatientCreate
    patient_data = PatientCreate(
        given_name="Hari",
        family_name="Prasad",
        dob=date(1990, 8, 15),
        phone="9988776655",
        email="hari@example.com",
        gender="male",
        abha_number="11112222333344",
        preferred_language="en-IN",
        address={"line1": "Road 1", "city": "Vijayawada", "state": "Andhra Pradesh", "postal_code": "520001"}
    )
    
    fhir_res = map_to_fhir_patient("uuid-test", patient_data)
    assert fhir_res["resourceType"] == "Patient"
    assert fhir_res["id"] == "uuid-test"
    assert fhir_res["gender"] == "male"
    assert str(fhir_res["birthDate"]) == "1990-08-15"
    assert len(fhir_res["name"]) == 1
    assert fhir_res["name"][0]["family"] == "Prasad"
    assert fhir_res["name"][0]["given"] == ["Hari"]
    
    # Assert ABHA linkage mapping
    abha_identifier = [i for i in fhir_res["identifier"] if "abha-number" in i["system"]][0]
    assert abha_identifier["value"] == "11112222333344"


def test_schema_validations():
    """Test validation constraints on schemas (e.g. ABHA length, Aadhaar length, referrers)."""
    # 1. Invalid ABHA length
    response = client.post(
        "/patients",
        headers={"Authorization": "Bearer dev.apollo.admin"},
        json={
            "given_name": "Ravi",
            "family_name": "Kumar",
            "abha_number": "123"  # Invalid
        }
    )
    assert response.status_code == 422
    assert "ABHA number" in response.text

    # 2. Invalid Aadhaar last four length
    response = client.post(
        "/patients",
        headers={"Authorization": "Bearer dev.apollo.admin"},
        json={
            "given_name": "Ravi",
            "family_name": "Kumar",
            "aadhaar_last_four": "12345"  # Invalid
        }
    )
    assert response.status_code == 422
    assert "Aadhaar last four" in response.text

    # 3. Referrer fields mismatch
    response = client.post(
        "/patients",
        headers={"Authorization": "Bearer dev.apollo.admin"},
        json={
            "given_name": "Ravi",
            "family_name": "Kumar",
            "referred_by_type": "clinic"  # missing referred_by_name
        }
    )
    assert response.status_code == 422
    assert "referred_by_name is required" in response.text

    # 4. Invalid email format
    response = client.post(
        "/patients",
        headers={"Authorization": "Bearer dev.apollo.admin"},
        json={
            "given_name": "Ravi",
            "family_name": "Kumar",
            "email": "invalid_email_at_com"
        }
    )
    assert response.status_code == 422
    assert "valid email format" in response.text

    # 5. Invalid phone format
    response = client.post(
        "/patients",
        headers={"Authorization": "Bearer dev.apollo.admin"},
        json={
            "given_name": "Ravi",
            "family_name": "Kumar",
            "phone": "12345"  # Too short, invalid format
        }
    )
    assert response.status_code == 422
    assert "valid format" in response.text


@patch("app.routers.patients.event_publish", new_callable=AsyncMock)
@patch("app.routers.patients.audit_record", new_callable=AsyncMock)
@patch("app.routers.patients.check_duplicate_patient", new_callable=AsyncMock)
def test_create_patient_success(mock_check_dupes, mock_audit, mock_event, db_session):
    """Test successful patient creation with event emission and database persist."""
    # Setup mocks
    mock_check_dupes.return_value = []
    
    # Mock gen_random_uuid
    mock_uuid_res = MagicMock()
    mock_uuid_res.mappings.return_value.one.return_value = {"val": "11111111-2222-3333-4444-555555555555"}
    
    # Mock INSERT RETURNING
    inserted_patient = {
        "id": "11111111-2222-3333-4444-555555555555",
        "given_name": "Sita",
        "family_name": "Devi",
        "dob": date(1992, 1, 1),
        "national_id": "9999",
        "phone": "9999999999",
        "abha_number": None,
        "abha_address": None,
        "aarogyasri_id": None,
        "pmjay_id": None,
        "aadhaar_last_four": None,
        "referred_by_type": None,
        "referred_by_name": None,
        "referred_by_id": None,
        "gender": "female",
        "email": "sita@example.com",
        "preferred_language": "te-IN",
        "address": None,
        "next_of_kin": None,
        "is_newborn": False,
        "mother_patient_id": None,
        "birth_time": None,
        "birth_weight_grams": None,
        "gestational_age_weeks": None,
        "multiple_birth_order": 1,
        "delivery_type": None,
        "apgar_score_1min": None,
        "apgar_score_5min": None,
        "fhir_resource": {"resourceType": "Patient"}
    }
    mock_insert_res = MagicMock()
    mock_insert_res.mappings.return_value.one.return_value = inserted_patient
    
    # 3 executes: 1 for SET LOCAL app.tenant_id, 1 for SELECT gen_random_uuid, 1 for INSERT
    db_session.execute.side_effect = [AsyncMock(), mock_uuid_res, mock_insert_res]

    response = client.post(
        "/patients",
        headers={"Authorization": "Bearer dev.apollo.admin"},
        json={
            "given_name": "Sita",
            "family_name": "Devi",
            "dob": "1992-01-01",
            "national_id": "9999",
            "phone": "9999999999",
            "gender": "female",
            "email": "sita@example.com",
            "preferred_language": "te-IN"
        }
    )

    assert response.status_code == 201
    res_data = response.json()
    assert res_data["id"] == "11111111-2222-3333-4444-555555555555"
    assert res_data["given_name"] == "Sita"
    assert res_data["gender"] == "female"
    
    # Assert Auditing & Event Emission triggers
    mock_audit.assert_called_once()
    mock_event.assert_called_once()


@patch("app.routers.patients.audit_record", new_callable=AsyncMock)
@patch("app.routers.patients.check_duplicate_patient", new_callable=AsyncMock)
def test_create_patient_duplicate_conflict(mock_check_dupes, mock_audit, db_session):
    """Test duplicate detection blocks patient creation and returns 409 Conflict."""
    mock_check_dupes.return_value = [
        {"id": "dupe-id", "given_name": "Sita", "family_name": "Devi", "dob": "1992-01-01", "phone": "9999999999", "score": 1.0}
    ]
    # mock execution for SET LOCAL app.tenant_id
    db_session.execute.return_value = AsyncMock()

    response = client.post(
        "/patients",
        headers={"Authorization": "Bearer dev.apollo.admin"},
        json={
            "given_name": "Sita",
            "family_name": "Devi",
            "dob": "1992-01-01",
            "phone": "9999999999"
        }
    )

    assert response.status_code == 409
    data = response.json()
    assert "Duplicate patient detected" in data["detail"]["message"]
    assert len(data["detail"]["candidates"]) == 1
    assert data["detail"]["candidates"][0]["id"] == "dupe-id"


@patch("app.routers.patients.event_publish", new_callable=AsyncMock)
@patch("app.routers.patients.audit_record", new_callable=AsyncMock)
@patch("app.routers.patients.check_duplicate_patient", new_callable=AsyncMock)
def test_create_patient_duplicate_override(mock_check_dupes, mock_audit, mock_event, db_session):
    """Test duplicate check bypass works when force=true is supplied."""
    mock_check_dupes.return_value = [
        {"id": "dupe-id", "given_name": "Sita", "family_name": "Devi", "dob": "1992-01-01", "phone": "9999999999", "score": 1.0}
    ]
    
    mock_uuid_res = MagicMock()
    mock_uuid_res.mappings.return_value.one.return_value = {"val": "override-uuid"}
    
    mock_insert_res = MagicMock()
    mock_insert_res.mappings.return_value.one.return_value = {
        "id": "override-uuid",
        "given_name": "Sita",
        "family_name": "Devi",
        "dob": date(1992, 1, 1),
        "national_id": None,
        "phone": "9999999999",
        "abha_number": None,
        "abha_address": None,
        "aarogyasri_id": None,
        "pmjay_id": None,
        "aadhaar_last_four": None,
        "referred_by_type": None,
        "referred_by_name": None,
        "referred_by_id": None,
        "gender": None,
        "email": None,
        "preferred_language": None,
        "address": None,
        "next_of_kin": None,
        "is_newborn": False,
        "mother_patient_id": None,
        "birth_time": None,
        "birth_weight_grams": None,
        "gestational_age_weeks": None,
        "multiple_birth_order": 1,
        "delivery_type": None,
        "apgar_score_1min": None,
        "apgar_score_5min": None,
        "fhir_resource": {"resourceType": "Patient"}
    }
    
    # 3 executes: SET LOCAL app.tenant_id, SELECT gen_random_uuid, INSERT
    db_session.execute.side_effect = [AsyncMock(), mock_uuid_res, mock_insert_res]

    # Post with force=true parameter
    response = client.post(
        "/patients?force=true",
        headers={"Authorization": "Bearer dev.apollo.admin"},
        json={
            "given_name": "Sita",
            "family_name": "Devi",
            "dob": "1992-01-01",
            "phone": "9999999999"
        }
    )

    assert response.status_code == 201
    assert response.json()["id"] == "override-uuid"
    
    # Assert check_duplicate_patient was NOT called because force=true bypasses it
    mock_check_dupes.assert_not_called()
    mock_audit.assert_called_once()
    mock_event.assert_called_once()


def test_least_privilege_roles():
    """Verify write endpoints deny access to non-admin/non-receptionist roles (IAM-002)."""
    response = client.post(
        "/patients",
        headers={"Authorization": "Bearer dev.apollo.physician"},  # Physician role has read-only access for patients registration
        json={
            "given_name": "Sita",
            "family_name": "Devi",
            "dob": "1992-01-01",
            "phone": "9999999999"
        }
    )
    assert response.status_code == 403
    assert "role 'physician' not permitted" in response.text


@pytest.mark.asyncio
async def test_newborn_duplicate_exemption():
    """Verify newborn registrations (is_newborn=True) are exempt from duplicate detection (REG-010)."""
    mock_session = AsyncMock(spec=AsyncSession)
    from app.schemas import PatientCreate
    
    newborn_data = PatientCreate(
        given_name="Baby of Lakshmi",
        family_name="Devi",
        dob=date(2026, 8, 29),
        phone="9876543210",  # Same as mother's phone
        is_newborn=True,
        mother_patient_id="11111111-1111-1111-1111-111111111111"
    )

    # Should immediately return empty list without running queries
    dupes = await check_duplicate_patient(mock_session, "tenantA", newborn_data)
    assert dupes == []
    mock_session.execute.assert_not_called()


def test_newborn_fhir_mapping():
    """Verify FHIR mapping properly incorporates birthTime, multipleBirth, and mother extensions for neonates."""
    from app.schemas import PatientCreate
    
    newborn_data = PatientCreate(
        given_name="Baby of Lakshmi",
        family_name="Devi",
        dob=date(2026, 8, 29),
        gender="male",
        phone="9876543210",
        is_newborn=True,
        mother_patient_id="11111111-1111-1111-1111-111111111111",
        birth_time="14:35",
        birth_weight_grams=2950,
        gestational_age_weeks=38,
        multiple_birth_order=1,
        delivery_type="cesarean_lscs",
        apgar_score_1min=8,
        apgar_score_5min=9
    )
    
    fhir_res = map_to_fhir_patient("newborn-uuid-1", newborn_data)
    assert fhir_res["resourceType"] == "Patient"
    assert fhir_res["id"] == "newborn-uuid-1"
    assert fhir_res["multipleBirthInteger"] == 1
    
    # Check birthTime extension
    extensions = fhir_res.get("extension", [])
    birth_time_ext = next((e for e in extensions if "patient-birthTime" in e["url"]), None)
    assert birth_time_ext is not None
    assert "2026-08-29" in str(birth_time_ext["valueDateTime"])
    assert "14:35" in str(birth_time_ext["valueDateTime"])
    
    # Check maternal name extension
    mother_ext = next((e for e in extensions if "patient-mothersMaidenName" in e["url"]), None)
    assert mother_ext is not None
    assert mother_ext["valueString"] == "Devi"


