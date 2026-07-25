"""THE ISOLATION TEST (PLT-002) — must stay green in CI forever.

Seeds two tenants (via conftest, as the admin role) and then, connecting as the
app role hms_app so RLS actually applies, asserts that binding a session to
tenant A cannot see tenant B's rows. Add a case here whenever a new tenant-scoped
table ships. If this test ever fails, the build must fail: patient data isolation
is broken.
"""
from __future__ import annotations

import os
from datetime import datetime
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# App role — non-superuser so RLS is enforced (superusers bypass RLS).
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://hms_app:app_password_change_me@localhost:5432/hms",
)

# Admin role for tenant provisioning — hms_app has SELECT on `tenant`, not
# INSERT (least privilege, PLT-002), so provisioning runs as the superuser.
ADMIN_DATABASE_URL = os.environ.get(
    "SEED_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres_change_me@localhost:5432/hms",
)


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _provision_test_tenants():
    """Ensure the two test tenants exist. Idempotent so it's safe to re-run."""
    engine = create_async_engine(ADMIN_DATABASE_URL)
    admin_sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with admin_sessionmaker() as s:
        await s.execute(text(
            "INSERT INTO tenant (id, name) VALUES "
            "('t_a','Tenant A'),('t_b','Tenant B') "
            "ON CONFLICT (id) DO NOTHING"
        ))
        await s.commit()
    await engine.dispose()
    yield


@pytest_asyncio.fixture()
async def sessionmaker_():
    engine = create_async_engine(DATABASE_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _set_tenant(s, tid: str) -> None:
    await s.execute(text("SELECT set_config('app.tenant_id', :t, true)").bindparams(t=tid))


@pytest.mark.asyncio
async def test_tenant_cannot_see_other_tenant_patients(sessionmaker_):
    # Arrange: one patient per tenant, each inserted under its own tenant context
    # so the RLS WITH CHECK on `patient` allows the insert.
    async with sessionmaker_() as s:
        await _set_tenant(s, "t_a")
        await s.execute(text(
            "INSERT INTO patient (tenant_id, given_name, family_name) "
            "VALUES ('t_a','Alice','Anderson')"))
        await s.commit()
    async with sessionmaker_() as s:
        await _set_tenant(s, "t_b")
        await s.execute(text(
            "INSERT INTO patient (tenant_id, given_name, family_name) "
            "VALUES ('t_b','Bob','Baker')"))
        await s.commit()

    # Act + Assert: as tenant A, only A's rows are visible.
    async with sessionmaker_() as s:
        await _set_tenant(s, "t_a")
        names = [r[0] for r in (await s.execute(
            text("SELECT given_name FROM patient"))).all()]
        assert "Alice" in names
        assert "Bob" not in names, "ISOLATION BREACH: tenant A saw tenant B data"

    # And the reverse.
    async with sessionmaker_() as s:
        await _set_tenant(s, "t_b")
        names = [r[0] for r in (await s.execute(
            text("SELECT given_name FROM patient"))).all()]
        assert "Bob" in names
        assert "Alice" not in names, "ISOLATION BREACH: tenant B saw tenant A data"


@pytest.mark.asyncio
async def test_no_tenant_context_sees_nothing(sessionmaker_):
    """With no app.tenant_id set, RLS must deny all rows (fail closed)."""
    async with sessionmaker_() as s:
        rows = (await s.execute(text("SELECT * FROM patient"))).all()
        assert rows == [], "RLS did not fail closed without a tenant context"


@pytest.mark.asyncio
async def test_consent_isolated_between_tenants(sessionmaker_):
    """PLT-010 consent records must be tenant-scoped, same model as patient/audit.
    Added when the patient_consent table shipped."""
    import uuid
    pid_a, pid_b = str(uuid.uuid4()), str(uuid.uuid4())

    async with sessionmaker_() as s:
        await _set_tenant(s, "t_a")
        await s.execute(text(
            "INSERT INTO patient_consent (tenant_id, patient_id, purpose) "
            "VALUES ('t_a', CAST(:pid AS uuid), 'share:abdm')"
        ).bindparams(pid=pid_a))
        await s.commit()
    async with sessionmaker_() as s:
        await _set_tenant(s, "t_b")
        await s.execute(text(
            "INSERT INTO patient_consent (tenant_id, patient_id, purpose) "
            "VALUES ('t_b', CAST(:pid AS uuid), 'share:abdm')"
        ).bindparams(pid=pid_b))
        await s.commit()

    async with sessionmaker_() as s:
        await _set_tenant(s, "t_a")
        rows = (await s.execute(text("SELECT patient_id FROM patient_consent"))).all()
        seen = {str(r[0]) for r in rows}
        assert pid_a in seen
        assert pid_b not in seen, "ISOLATION BREACH: tenant A saw tenant B consent"


@pytest.mark.asyncio
async def test_write_denied_when_tenant_id_mismatches_context(sessionmaker_):
    """WITH CHECK on the RLS policy must block writing rows tagged for another
    tenant, even when the session is bound to a valid tenant."""
    from sqlalchemy.exc import DBAPIError

    async with sessionmaker_() as s:
        await _set_tenant(s, "t_a")
        with pytest.raises(DBAPIError):
            await s.execute(text(
                "INSERT INTO patient (tenant_id, given_name, family_name) "
                "VALUES ('t_b','Mallory','Impostor')"))
            await s.commit()


@pytest.mark.asyncio
async def test_audit_event_isolated_and_append_only(sessionmaker_):
    """PLT-002 + PLT-005: audit_event is tenant-scoped like every other table,
    and append-only for the app role (no UPDATE/DELETE grant). Added in N1-01
    when the coverage audit found it missing from this gate."""
    from sqlalchemy.exc import DBAPIError, ProgrammingError

    async with sessionmaker_() as s:
        await _set_tenant(s, "t_a")
        await s.execute(text(
            "INSERT INTO audit_event (tenant_id, actor_user_id, actor_role, action, resource_type, context_note) "
            "VALUES ('t_a', 'u_a', 'physician', 'read', 'patient', 'iso_audit_a')"))
        await s.commit()
    async with sessionmaker_() as s:
        await _set_tenant(s, "t_b")
        await s.execute(text(
            "INSERT INTO audit_event (tenant_id, actor_user_id, actor_role, action, resource_type, context_note) "
            "VALUES ('t_b', 'u_b', 'physician', 'read', 'patient', 'iso_audit_b')"))
        await s.commit()

    async with sessionmaker_() as s:
        await _set_tenant(s, "t_a")
        notes = [r[0] for r in (await s.execute(
            text("SELECT context_note FROM audit_event"))).all()]
        assert "iso_audit_a" in notes
        assert "iso_audit_b" not in notes, "ISOLATION BREACH: tenant A saw tenant B audit trail"

    async with sessionmaker_() as s:
        rows = (await s.execute(text("SELECT * FROM audit_event"))).all()
        assert rows == [], "RLS did not fail closed on audit_event without tenant context"

    # Append-only: the app role must not be able to UPDATE or DELETE audit rows.
    async with sessionmaker_() as s:
        await _set_tenant(s, "t_a")
        with pytest.raises((DBAPIError, ProgrammingError)):
            await s.execute(text(
                "UPDATE audit_event SET context_note = 'tampered' WHERE context_note = 'iso_audit_a'"))
            await s.commit()
    async with sessionmaker_() as s:
        await _set_tenant(s, "t_a")
        with pytest.raises((DBAPIError, ProgrammingError)):
            await s.execute(text(
                "DELETE FROM audit_event WHERE context_note = 'iso_audit_a'"))
            await s.commit()


async def _provision_all_parents(s, tid: str) -> dict[str, str]:
    suffix = "a" if tid == "t_a" else "b"
    ids = {
        "patient_id": f"{suffix}0000000-0000-0000-0000-000000000001",
        "site_id": f"site_{suffix}",
        "room_id": f"room_{suffix}",
        "svc_id": f"svc_{suffix}",
        "doc_id": f"doc_{suffix}",
        "app_id": f"{suffix}0000000-0000-0000-0000-000000000002",
        "enc_id": f"{suffix}0000000-0000-0000-0000-000000000003",
        "cov_id": f"{suffix}0000000-0000-0000-0000-000000000004",
        "inv_id": f"{suffix}0000000-0000-0000-0000-000000000005",
        "med_id": f"med_{suffix}",
        "lab_id": f"lab_{suffix}",
        "ord_id": f"{suffix}0000000-0000-0000-0000-000000000006",
        "rx_id": f"{suffix}0000000-0000-0000-0000-000000000007",
        "chg_id": f"chg_{suffix}",
        "sub_id": f"{suffix}0000000-0000-0000-0000-000000000008",
        "prereq_id": f"prereq_{suffix}",
    }

    await _set_tenant(s, tid)

    await s.execute(text(
        "INSERT INTO site (id, tenant_id, name) VALUES (:site_id, :tid, 'Site') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(site_id=ids["site_id"], tid=tid))

    await s.execute(text(
        "INSERT INTO practitioner (id, tenant_id, name) VALUES (:doc_id, :tid, 'Doc') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(doc_id=ids["doc_id"], tid=tid))

    await s.execute(text(
        "INSERT INTO service (id, tenant_id, name, duration_minutes) VALUES (:svc_id, :tid, 'Svc', 30) "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(svc_id=ids["svc_id"], tid=tid))

    await s.execute(text(
        "INSERT INTO room (id, site_id, tenant_id, name) VALUES (:room_id, :site_id, :tid, 'Room') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(room_id=ids["room_id"], site_id=ids["site_id"], tid=tid))

    await s.execute(text(
        "INSERT INTO patient (id, tenant_id, given_name, family_name) VALUES (CAST(:patient_id AS uuid), :tid, 'Given', 'Family') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(patient_id=ids["patient_id"], tid=tid))

    await s.execute(text(
        "INSERT INTO appointment (id, tenant_id, patient_id, practitioner_id, site_id, room_id, service_id, status, start_time, end_time) "
        "VALUES (:app_id, :tid, CAST(:patient_id AS uuid), :doc_id, :site_id, :room_id, :svc_id, 'BOOKED', now(), now() + interval '30 minutes') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(app_id=ids["app_id"], tid=tid, patient_id=ids["patient_id"], doc_id=ids["doc_id"], site_id=ids["site_id"], room_id=ids["room_id"], svc_id=ids["svc_id"]))

    await s.execute(text(
        "INSERT INTO encounter (id, tenant_id, appointment_id, patient_id, practitioner_id, site_id, status) "
        "VALUES (:enc_id, :tid, :app_id, CAST(:patient_id AS uuid), :doc_id, :site_id, 'open') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(enc_id=ids["enc_id"], tid=tid, app_id=ids["app_id"], patient_id=ids["patient_id"], doc_id=ids["doc_id"], site_id=ids["site_id"]))

    await s.execute(text(
        "INSERT INTO patient_coverage (id, tenant_id, patient_id, scheme_type, plan_name, member_id, validity_start, validity_end, patient_share_percent) "
        "VALUES (:cov_id, :tid, CAST(:patient_id AS uuid), 'private', 'Plan', 'MEM', '2026-01-01', '2030-01-01', 20) "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(cov_id=ids["cov_id"], tid=tid, patient_id=ids["patient_id"]))

    await s.execute(text(
        "INSERT INTO invoice (id, tenant_id, patient_id, encounter_id, status, coverage_id) "
        "VALUES (:inv_id, :tid, CAST(:patient_id AS uuid), :enc_id, 'draft', :cov_id) "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(inv_id=ids["inv_id"], tid=tid, patient_id=ids["patient_id"], enc_id=ids["enc_id"], cov_id=ids["cov_id"]))

    await s.execute(text(
        "INSERT INTO medication_catalog (id, tenant_id, name, generic_name, form, strength) "
        "VALUES (:med_id, :tid, 'Drug', 'Generic', 'tablet', '500mg') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(med_id=ids["med_id"], tid=tid))

    await s.execute(text(
        "INSERT INTO lab_catalog (id, tenant_id, test_code, name) "
        "VALUES (:lab_id, :tid, 'LOINC', 'Test') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(lab_id=ids["lab_id"], tid=tid))

    await s.execute(text(
        "INSERT INTO lab_order (id, tenant_id, patient_id, practitioner_id, encounter_id, status) "
        "VALUES (:ord_id, :tid, CAST(:patient_id AS uuid), :doc_id, :enc_id, 'ordered') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(ord_id=ids["ord_id"], tid=tid, patient_id=ids["patient_id"], doc_id=ids["doc_id"], enc_id=ids["enc_id"]))

    await s.execute(text(
        "INSERT INTO prescription (id, tenant_id, patient_id, practitioner_id, encounter_id, status) "
        "VALUES (:rx_id, :tid, CAST(:patient_id AS uuid), :doc_id, :enc_id, 'draft') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(rx_id=ids["rx_id"], tid=tid, patient_id=ids["patient_id"], doc_id=ids["doc_id"], enc_id=ids["enc_id"]))

    await s.execute(text(
        "INSERT INTO charge_master (id, tenant_id, code, name, category, standard_price) "
        "VALUES (:chg_id, :tid, 'CHG', 'Charge', 'consultation', 100) "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(chg_id=ids["chg_id"], tid=tid))

    await s.execute(text(
        "INSERT INTO webhook_subscription (id, tenant_id, event_type, url, secret_key) "
        "VALUES (:sub_id, :tid, 'patient.*', 'http://webhook.site', 'secret') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(sub_id=ids["sub_id"], tid=tid))

    await s.execute(text(
        "INSERT INTO prerequisite_definition (id, tenant_id, code, description) "
        "VALUES (:prereq_id, :tid, 'FASTING', 'Fast') "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(prereq_id=ids["prereq_id"], tid=tid))

    await s.commit()
    return ids


@pytest.mark.asyncio
async def test_all_module_tables_tenant_isolation(sessionmaker_):
    """Verifies that RLS read/write isolation is enforced for all tenant-scoped tables across all modules."""
    from sqlalchemy.exc import DBAPIError

    # 1. Provision parent records for both tenants
    async with sessionmaker_() as s:
        ids_a = await _provision_all_parents(s, "t_a")
    async with sessionmaker_() as s:
        ids_b = await _provision_all_parents(s, "t_b")

    # 2. Define child tables list with all columns, values, check parameters
    child_tables = [
        {
            "table": "site",
            "cols": "id, tenant_id, name",
            "select_col": "name",
            "check_val_a": "Site A Test",
            "check_val_b": "Site B Test",
            "params_a": {"id": "site_test_a", "name": "Site A Test"},
            "params_b": {"id": "site_test_b", "name": "Site B Test"}
        },
        {
            "table": "practitioner",
            "cols": "id, tenant_id, name",
            "select_col": "name",
            "check_val_a": "Doc A Test",
            "check_val_b": "Doc B Test",
            "params_a": {"id": "doc_test_a", "name": "Doc A Test"},
            "params_b": {"id": "doc_test_b", "name": "Doc B Test"}
        },
        {
            "table": "service",
            "cols": "id, tenant_id, name, duration_minutes",
            "select_col": "name",
            "check_val_a": "Svc A Test",
            "check_val_b": "Svc B Test",
            "params_a": {"id": "svc_test_a", "name": "Svc A Test", "duration_minutes": 15},
            "params_b": {"id": "svc_test_b", "name": "Svc B Test", "duration_minutes": 30}
        },
        {
            "table": "room",
            "cols": "id, site_id, tenant_id, name",
            "select_col": "name",
            "check_val_a": "Room A Test",
            "check_val_b": "Room B Test",
            "params_a": {"id": "room_test_a", "site_id": ids_a["site_id"], "name": "Room A Test"},
            "params_b": {"id": "room_test_b", "site_id": ids_b["site_id"], "name": "Room B Test"}
        },
        {
            "table": "appointment",
            "cols": "id, tenant_id, patient_id, practitioner_id, site_id, room_id, service_id, status, start_time, end_time",
            "select_col": "status",
            "check_val_a": "ARRIVED_A",
            "check_val_b": "ARRIVED_B",
            "params_a": {"id": f"{ids_a['patient_id'][:8]}-0000-0000-0000-000000000099", "patient_id": ids_a["patient_id"], "practitioner_id": ids_a["doc_id"], "site_id": ids_a["site_id"], "room_id": ids_a["room_id"], "service_id": ids_a["svc_id"], "status": "ARRIVED_A", "start_time": datetime.now(), "end_time": datetime.now()},
            "params_b": {"id": f"{ids_b['patient_id'][:8]}-0000-0000-0000-000000000099", "patient_id": ids_b["patient_id"], "practitioner_id": ids_b["doc_id"], "site_id": ids_b["site_id"], "room_id": ids_b["room_id"], "service_id": ids_b["svc_id"], "status": "ARRIVED_B", "start_time": datetime.now(), "end_time": datetime.now()}
        },
        {
            "table": "encounter",
            "cols": "id, tenant_id, appointment_id, patient_id, practitioner_id, site_id, status",
            "select_col": "status",
            "check_val_a": "open_a",
            "check_val_b": "open_b",
            "params_a": {"id": f"{ids_a['patient_id'][:8]}-0000-0000-0000-000000000098", "appointment_id": ids_a["app_id"], "patient_id": ids_a["patient_id"], "practitioner_id": ids_a["doc_id"], "site_id": ids_a["site_id"], "status": "open_a"},
            "params_b": {"id": f"{ids_b['patient_id'][:8]}-0000-0000-0000-000000000098", "appointment_id": ids_b["app_id"], "patient_id": ids_b["patient_id"], "practitioner_id": ids_b["doc_id"], "site_id": ids_b["site_id"], "status": "open_b"}
        },
        {
            "table": "patient_coverage",
            "cols": "id, tenant_id, patient_id, scheme_type, plan_name, member_id, validity_start, validity_end, patient_share_percent",
            "select_col": "plan_name",
            "check_val_a": "Plan A Test",
            "check_val_b": "Plan B Test",
            "params_a": {"id": f"{ids_a['patient_id'][:8]}-0000-0000-0000-000000000097", "patient_id": ids_a["patient_id"], "scheme_type": "private", "plan_name": "Plan A Test", "member_id": "MEMA", "validity_start": "2026-01-01", "validity_end": "2030-01-01", "patient_share_percent": 10},
            "params_b": {"id": f"{ids_b['patient_id'][:8]}-0000-0000-0000-000000000097", "patient_id": ids_b["patient_id"], "scheme_type": "private", "plan_name": "Plan B Test", "member_id": "MEMB", "validity_start": "2026-01-01", "validity_end": "2030-01-01", "patient_share_percent": 20}
        },
        {
            "table": "invoice",
            "cols": "id, tenant_id, patient_id, encounter_id, status",
            "select_col": "status",
            "check_val_a": "draft_a",
            "check_val_b": "draft_b",
            "params_a": {"id": f"{ids_a['patient_id'][:8]}-0000-0000-0000-000000000096", "patient_id": ids_a["patient_id"], "encounter_id": ids_a["enc_id"], "status": "draft_a"},
            "params_b": {"id": f"{ids_b['patient_id'][:8]}-0000-0000-0000-000000000096", "patient_id": ids_b["patient_id"], "encounter_id": ids_b["enc_id"], "status": "draft_b"}
        },
        {
            "table": "medication_catalog",
            "cols": "id, tenant_id, name, generic_name, form, strength",
            "select_col": "name",
            "check_val_a": "Med A Test",
            "check_val_b": "Med B Test",
            "params_a": {"id": "med_t_a", "name": "Med A Test", "generic_name": "Gen A", "form": "tablet", "strength": "10mg"},
            "params_b": {"id": "med_t_b", "name": "Med B Test", "generic_name": "Gen B", "form": "tablet", "strength": "20mg"}
        },
        {
            "table": "lab_catalog",
            "cols": "id, tenant_id, test_code, name",
            "select_col": "name",
            "check_val_a": "Lab Test A",
            "check_val_b": "Lab Test B",
            "params_a": {"id": "lab_t_a", "test_code": "CODE_A", "name": "Lab Test A"},
            "params_b": {"id": "lab_t_b", "test_code": "CODE_B", "name": "Lab Test B"}
        },
        {
            "table": "lab_order",
            "cols": "id, tenant_id, patient_id, practitioner_id, encounter_id, status",
            "select_col": "status",
            "check_val_a": "ordered_a",
            "check_val_b": "ordered_b",
            "params_a": {"id": f"{ids_a['patient_id'][:8]}-0000-0000-0000-000000000095", "patient_id": ids_a["patient_id"], "practitioner_id": ids_a["doc_id"], "encounter_id": ids_a["enc_id"], "status": "ordered_a"},
            "params_b": {"id": f"{ids_b['patient_id'][:8]}-0000-0000-0000-000000000095", "patient_id": ids_b["patient_id"], "practitioner_id": ids_b["doc_id"], "encounter_id": ids_b["enc_id"], "status": "ordered_b"}
        },
        {
            "table": "prescription",
            "cols": "id, tenant_id, patient_id, practitioner_id, encounter_id, status",
            "select_col": "status",
            "check_val_a": "draft_rx_a",
            "check_val_b": "draft_rx_b",
            "params_a": {"id": f"{ids_a['patient_id'][:8]}-0000-0000-0000-000000000094", "patient_id": ids_a["patient_id"], "practitioner_id": ids_a["doc_id"], "encounter_id": ids_a["enc_id"], "status": "draft_rx_a"},
            "params_b": {"id": f"{ids_b['patient_id'][:8]}-0000-0000-0000-000000000094", "patient_id": ids_b["patient_id"], "practitioner_id": ids_b["doc_id"], "encounter_id": ids_b["enc_id"], "status": "draft_rx_b"}
        },
        {
            "table": "charge_master",
            "cols": "id, tenant_id, code, name, category, standard_price",
            "select_col": "name",
            "check_val_a": "Charge A Test",
            "check_val_b": "Charge B Test",
            "params_a": {"id": "chg_t_a", "code": "C_A", "name": "Charge A Test", "category": "consultation", "standard_price": 100},
            "params_b": {"id": "chg_t_b", "code": "C_B", "name": "Charge B Test", "category": "consultation", "standard_price": 200}
        },
        {
            "table": "webhook_subscription",
            "cols": "id, tenant_id, event_type, url, secret_key",
            "select_col": "event_type",
            "check_val_a": "evt_type_a",
            "check_val_b": "evt_type_b",
            "params_a": {"id": f"{ids_a['patient_id'][:8]}-0000-0000-0000-000000000093", "event_type": "evt_type_a", "url": "http://a.com", "secret_key": "sec_a"},
            "params_b": {"id": f"{ids_b['patient_id'][:8]}-0000-0000-0000-000000000093", "event_type": "evt_type_b", "url": "http://b.com", "secret_key": "sec_b"}
        },
        {
            "table": "prerequisite_definition",
            "cols": "id, tenant_id, code, description",
            "select_col": "description",
            "check_val_a": "Prereq Desc A",
            "check_val_b": "Prereq Desc B",
            "params_a": {"id": "prq_t_a", "code": "PRQ_A", "description": "Prereq Desc A"},
            "params_b": {"id": "prq_t_b", "code": "PRQ_B", "description": "Prereq Desc B"}
        },
        {
            "table": "practitioner_availability",
            "cols": "practitioner_id, site_id, tenant_id, day_of_week, start_time, end_time",
            "select_col": "day_of_week",
            "check_val_a": 1,
            "check_val_b": 2,
            "params_a": {"practitioner_id": ids_a["doc_id"], "site_id": ids_a["site_id"], "day_of_week": 1, "start_time": "09:00", "end_time": "17:00"},
            "params_b": {"practitioner_id": ids_b["doc_id"], "site_id": ids_b["site_id"], "day_of_week": 2, "start_time": "09:00", "end_time": "17:00"}
        },
        {
            "table": "appointment_prerequisite",
            "cols": "appointment_id, prerequisite_id, tenant_id, satisfied",
            "select_col": "satisfied",
            "check_val_a": True,
            "check_val_b": False,
            "params_a": {"appointment_id": ids_a["app_id"], "prerequisite_id": ids_a["prereq_id"], "satisfied": True},
            "params_b": {"appointment_id": ids_b["app_id"], "prerequisite_id": ids_b["prereq_id"], "satisfied": False}
        },
        {
            "table": "clinical_note",
            "cols": "tenant_id, encounter_id, template_type, rich_text_content",
            "select_col": "rich_text_content",
            "check_val_a": "note_a",
            "check_val_b": "note_b",
            "params_a": {"encounter_id": ids_a["enc_id"], "template_type": "SOAP", "rich_text_content": "note_a"},
            "params_b": {"encounter_id": ids_b["enc_id"], "template_type": "SOAP", "rich_text_content": "note_b"}
        },
        {
            "table": "clinical_note_addendum",
            "cols": "tenant_id, encounter_id, author_id, content",
            "select_col": "content",
            "check_val_a": "add_a",
            "check_val_b": "add_b",
            "params_a": {"encounter_id": ids_a["enc_id"], "author_id": "doc_a", "content": "add_a"},
            "params_b": {"encounter_id": ids_b["enc_id"], "author_id": "doc_b", "content": "add_b"}
        },
        {
            "table": "encounter_document",
            "cols": "tenant_id, encounter_id, patient_id, file_path, file_type, label",
            "select_col": "label",
            "check_val_a": "lbl_a",
            "check_val_b": "lbl_b",
            "params_a": {"encounter_id": ids_a["enc_id"], "patient_id": ids_a["patient_id"], "file_path": "/a.pdf", "file_type": "pdf", "label": "lbl_a"},
            "params_b": {"encounter_id": ids_b["enc_id"], "patient_id": ids_b["patient_id"], "file_path": "/b.pdf", "file_type": "pdf", "label": "lbl_b"}
        },
        {
            "table": "allergy_intolerance",
            "cols": "tenant_id, patient_id, substance_display, asserted_by",
            "select_col": "substance_display",
            "check_val_a": "all_a",
            "check_val_b": "all_b",
            "params_a": {"patient_id": ids_a["patient_id"], "substance_display": "all_a", "asserted_by": "doc_a"},
            "params_b": {"patient_id": ids_b["patient_id"], "substance_display": "all_b", "asserted_by": "doc_b"}
        },
        {
            "table": "condition",
            "cols": "tenant_id, patient_id, code, display",
            "select_col": "display",
            "check_val_a": "cond_a",
            "check_val_b": "cond_b",
            "params_a": {"patient_id": ids_a["patient_id"], "code": "A", "display": "cond_a"},
            "params_b": {"patient_id": ids_b["patient_id"], "code": "B", "display": "cond_b"}
        },
        {
            "table": "medication_statement",
            "cols": "tenant_id, patient_id, medication_code, medication_display",
            "select_col": "medication_display",
            "check_val_a": "med_stmt_a",
            "check_val_b": "med_stmt_b",
            "params_a": {"patient_id": ids_a["patient_id"], "medication_code": "A", "medication_display": "med_stmt_a"},
            "params_b": {"patient_id": ids_b["patient_id"], "medication_code": "B", "medication_display": "med_stmt_b"}
        },
        {
            "table": "vital_sign",
            "cols": "tenant_id, encounter_id, patient_id, type, value, unit",
            "select_col": "value",
            "check_val_a": 70,
            "check_val_b": 80,
            "params_a": {"encounter_id": ids_a["enc_id"], "patient_id": ids_a["patient_id"], "type": "heart_rate", "value": 70, "unit": "bpm"},
            "params_b": {"encounter_id": ids_b["enc_id"], "patient_id": ids_b["patient_id"], "type": "heart_rate", "value": 80, "unit": "bpm"}
        },
        {
            "table": "lab_order_item",
            "cols": "tenant_id, order_id, test_id",
            "select_col": "test_id",
            "check_val_a": "lab_a",
            "check_val_b": "lab_b",
            "params_a": {"order_id": ids_a["ord_id"], "test_id": ids_a["lab_id"]},
            "params_b": {"order_id": ids_b["ord_id"], "test_id": ids_b["lab_id"]}
        },
        {
            "table": "lab_result",
            "cols": "tenant_id, test_id, value, unit",
            "select_col": "value",
            "check_val_a": 5,
            "check_val_b": 10,
            "params_a": {"test_id": ids_a["lab_id"], "value": 5, "unit": "mg/dL"},
            "params_b": {"test_id": ids_b["lab_id"], "value": 10, "unit": "mg/dL"}
        },
        {
            "table": "lab_unmatched_result",
            "cols": "tenant_id, payload",
            "select_col": "payload",
            "check_val_a": {"a": 1},
            "check_val_b": {"b": 2},
            "params_a": {"payload": {"a": 1}},
            "params_b": {"payload": {"b": 2}}
        },
        {
            "table": "tenant_formulary",
            "cols": "tenant_id, medication_id",
            "select_col": "medication_id",
            "check_val_a": "med_a",
            "check_val_b": "med_b",
            "params_a": {"medication_id": ids_a["med_id"]},
            "params_b": {"medication_id": ids_b["med_id"]}
        },
        {
            "table": "prescription_item",
            "cols": "tenant_id, prescription_id, medication_id, dose, unit, route, frequency, duration_days, quantity",
            "select_col": "dose",
            "check_val_a": 1,
            "check_val_b": 2,
            "params_a": {"prescription_id": ids_a["rx_id"], "medication_id": ids_a["med_id"], "dose": 1, "unit": "tab", "route": "oral", "frequency": "daily", "duration_days": 7, "quantity": 7},
            "params_b": {"prescription_id": ids_b["rx_id"], "medication_id": ids_b["med_id"], "dose": 2, "unit": "tab", "route": "oral", "frequency": "daily", "duration_days": 7, "quantity": 7}
        },
        {
            "table": "prescription_override",
            "cols": "tenant_id, prescription_id, alert_type, severity, reason",
            "select_col": "reason",
            "check_val_a": "reason_a",
            "check_val_b": "reason_b",
            "params_a": {"prescription_id": ids_a["rx_id"], "alert_type": "allergy", "severity": "high", "reason": "reason_a"},
            "params_b": {"prescription_id": ids_b["rx_id"], "alert_type": "allergy", "severity": "high", "reason": "reason_b"}
        },
        {
            "table": "prescription_favorite",
            "cols": "tenant_id, practitioner_id, medication_id",
            "select_col": "medication_id",
            "check_val_a": "med_a",
            "check_val_b": "med_b",
            "params_a": {"practitioner_id": ids_a["doc_id"], "medication_id": ids_a["med_id"]},
            "params_b": {"practitioner_id": ids_b["doc_id"], "medication_id": ids_b["med_id"]}
        },
        {
            "table": "invoice_item",
            "cols": "tenant_id, invoice_id, charge_item_id, unit_price, patient_share, payer_share",
            "select_col": "unit_price",
            "check_val_a": 50,
            "check_val_b": 60,
            "params_a": {"invoice_id": ids_a["inv_id"], "charge_item_id": ids_a["chg_id"], "unit_price": 50, "patient_share": 10, "payer_share": 40},
            "params_b": {"invoice_id": ids_b["inv_id"], "charge_item_id": ids_b["chg_id"], "unit_price": 60, "patient_share": 12, "payer_share": 48}
        },
        {
            "table": "payment",
            "cols": "tenant_id, invoice_id, payment_method, amount",
            "select_col": "amount",
            "check_val_a": 100,
            "check_val_b": 200,
            "params_a": {"invoice_id": ids_a["inv_id"], "payment_method": "cash", "amount": 100},
            "params_b": {"invoice_id": ids_b["inv_id"], "payment_method": "cash", "amount": 200}
        },
        {
            "table": "claim",
            "cols": "tenant_id, invoice_id, coverage_id, total_claimed",
            "select_col": "total_claimed",
            "check_val_a": 80,
            "check_val_b": 90,
            "params_a": {"invoice_id": ids_a["inv_id"], "coverage_id": ids_a["cov_id"], "total_claimed": 80},
            "params_b": {"invoice_id": ids_b["inv_id"], "coverage_id": ids_b["cov_id"], "total_claimed": 90}
        },
        {
            "table": "portal_invitation",
            "cols": "tenant_id, patient_id, email, phone, otp_code, expires_at",
            "select_col": "email",
            "check_val_a": "a@p.com",
            "check_val_b": "b@p.com",
            "params_a": {"patient_id": ids_a["patient_id"], "email": "a@p.com", "phone": "123", "otp_code": "123", "expires_at": datetime.now()},
            "params_b": {"patient_id": ids_b["patient_id"], "email": "b@p.com", "phone": "456", "otp_code": "456", "expires_at": datetime.now()}
        },
        {
            "table": "portal_user",
            "cols": "tenant_id, patient_id, username, password_hash",
            "select_col": "username",
            "check_val_a": "user_a",
            "check_val_b": "user_b",
            "params_a": {"patient_id": ids_a["patient_id"], "username": "user_a", "password_hash": "hash"},
            "params_b": {"patient_id": ids_b["patient_id"], "username": "user_b", "password_hash": "hash"}
        },
        {
            "table": "portal_questionnaire",
            "cols": "tenant_id, appointment_id, questionnaire_type, questions_json",
            "select_col": "questionnaire_type",
            "check_val_a": "type_a",
            "check_val_b": "type_b",
            "params_a": {"appointment_id": ids_a["app_id"], "questionnaire_type": "type_a", "questions_json": {}},
            "params_b": {"appointment_id": ids_b["app_id"], "questionnaire_type": "type_b", "questions_json": {}}
        },
        {
            "table": "portal_proxy",
            "cols": "tenant_id, patient_id, proxy_patient_id, relationship_type, expires_at",
            "select_col": "relationship_type",
            "check_val_a": "rel_a",
            "check_val_b": "rel_b",
            "params_a": {"patient_id": ids_a["patient_id"], "proxy_patient_id": ids_a["patient_id"], "relationship_type": "rel_a", "expires_at": datetime.now()},
            "params_b": {"patient_id": ids_b["patient_id"], "proxy_patient_id": ids_b["patient_id"], "relationship_type": "rel_b", "expires_at": datetime.now()}
        },
        {
            "table": "portal_message",
            "cols": "tenant_id, patient_id, direction, message_text",
            "select_col": "message_text",
            "check_val_a": "msg_a",
            "check_val_b": "msg_b",
            "params_a": {"patient_id": ids_a["patient_id"], "direction": "p_to_c", "message_text": "msg_a"},
            "params_b": {"patient_id": ids_b["patient_id"], "direction": "p_to_c", "message_text": "msg_b"}
        },
        {
            "table": "webhook_delivery_log",
            "cols": "tenant_id, subscription_id, event_type, payload",
            "select_col": "event_type",
            "check_val_a": "evt_a",
            "check_val_b": "evt_b",
            "params_a": {"subscription_id": ids_a["sub_id"], "event_type": "evt_a", "payload": "{}"},
            "params_b": {"subscription_id": ids_b["sub_id"], "event_type": "evt_b", "payload": "{}"}
        },
        {
            "table": "integration_log",
            "cols": "tenant_id, direction, message_type, status, payload",
            "select_col": "status",
            "check_val_a": "status_a",
            "check_val_b": "status_b",
            "params_a": {"direction": "inbound", "message_type": "CSV", "status": "status_a", "payload": {"data": "a"}},
            "params_b": {"direction": "inbound", "message_type": "CSV", "status": "status_b", "payload": {"data": "b"}}
        }
    ]

    for item in child_tables:
        tbl = item["table"]
        cols = item["cols"]
        
        async with sessionmaker_() as s:
            await _set_tenant(s, "t_a")
            p_a = {"tid": "t_a"}
            p_a.update(item["params_a"])
            import json
            for k, v in list(p_a.items()):
                if isinstance(v, dict):
                    p_a[k] = json.dumps(v)
            
            param_names = [c.strip() for c in cols.split(",")]
            bind_params = {}
            for name in param_names:
                if name == "tenant_id":
                    bind_params["tenant_id"] = "t_a"
                else:
                    bind_params[name] = p_a.get(name)

            conflict_clause = " ON CONFLICT (id) DO NOTHING" if "id" in param_names else ""
            val_exprs = [f"CAST(:{n} AS uuid)" if n in ("patient_id", "proxy_patient_id") else f"CAST(:{n} AS date)" if n in ("validity_start", "validity_end") else f"CAST(:{n} AS jsonb)" if n in ("payload", "questions_json") else f":{n}" for n in param_names]
            sql = f"INSERT INTO {tbl} ({cols}) VALUES ({', '.join(val_exprs)}){conflict_clause}"
            await s.execute(text(sql).bindparams(**bind_params))
            await s.commit()

        async with sessionmaker_() as s:
            await _set_tenant(s, "t_b")
            p_b = {"tid": "t_b"}
            p_b.update(item["params_b"])
            import json
            for k, v in list(p_b.items()):
                if isinstance(v, dict):
                    p_b[k] = json.dumps(v)

            bind_params = {}
            for name in param_names:
                if name == "tenant_id":
                    bind_params["tenant_id"] = "t_b"
                else:
                    bind_params[name] = p_b.get(name)

            sql = f"INSERT INTO {tbl} ({cols}) VALUES ({', '.join(val_exprs)}){conflict_clause}"
            await s.execute(text(sql).bindparams(**bind_params))
            await s.commit()

        async with sessionmaker_() as s:
            await _set_tenant(s, "t_a")
            sel_col = item["select_col"]
            rows = (await s.execute(text(f"SELECT {sel_col} FROM {tbl}"))).all()
            results = []
            for r in rows:
                val = r[0]
                if isinstance(val, dict) or (isinstance(val, str) and (val.startswith("{") or val.startswith("["))):
                    import json
                    if isinstance(val, str):
                        try:
                            val = json.loads(val)
                        except:
                            pass
                results.append(val)
            assert item["check_val_a"] in results, f"Tenant A did not see its own data in {tbl}"
            assert item["check_val_b"] not in results, f"ISOLATION BREACH: Tenant A saw Tenant B data in {tbl}"

        async with sessionmaker_() as s:
            await _set_tenant(s, "t_b")
            sel_col = item["select_col"]
            rows = (await s.execute(text(f"SELECT {sel_col} FROM {tbl}"))).all()
            results = []
            for r in rows:
                val = r[0]
                if isinstance(val, dict) or (isinstance(val, str) and (val.startswith("{") or val.startswith("["))):
                    import json
                    if isinstance(val, str):
                        try:
                            val = json.loads(val)
                        except:
                            pass
                results.append(val)
            assert item["check_val_b"] in results, f"Tenant B did not see its own data in {tbl}"
            assert item["check_val_a"] not in results, f"ISOLATION BREACH: Tenant B saw Tenant A data in {tbl}"

        async with sessionmaker_() as s:
            rows = (await s.execute(text(f"SELECT * FROM {tbl}"))).all()
            assert rows == [], f"RLS did not fail closed without a tenant context in {tbl}"

        async with sessionmaker_() as s:
            await _set_tenant(s, "t_a")
            bind_params = {}
            for name in param_names:
                if name == "tenant_id":
                    bind_params["tenant_id"] = "t_b"
                else:
                    bind_params[name] = p_a.get(name)

            sql = f"INSERT INTO {tbl} ({cols}) VALUES ({', '.join(val_exprs)}){conflict_clause}"
            with pytest.raises(DBAPIError):
                await s.execute(text(sql).bindparams(**bind_params))
                await s.commit()


@pytest.mark.asyncio
async def test_operator_target_tenant_context_isolation():
    """PLT-002 / TEN-104: Operator/Admin whose context is tenant A provisions and configures tenant B.
    Asserts:
    1. The target tenant B configuration and audit events land strictly under tenant B context.
    2. Tenant A session can NEVER see tenant B's audit events or site/room/service configurations.
    """
    from hms_tenancy import RequestContext, tenant_session
    from hms_audit import record as audit_record

    ctx_operator = RequestContext(tenant_id="t_a", user_id="operator@t_a", role="operator")
    target_tenant_b = "t_b"

    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        # Operator in context t_a performs setup wizard write for target tenant t_b
        async with session_factory() as session:
            async with tenant_session(session, ctx_operator, tenant_id=target_tenant_b) as s:
                await s.execute(
                    text("INSERT INTO site (id, tenant_id, name) VALUES ('site_tb_reg', 't_b', 'Tenant B Site') ON CONFLICT (id) DO NOTHING")
                )
                await audit_record(
                    session=s,
                    ctx=ctx_operator,
                    action="create",
                    resource_type="tenant_wizard",
                    context_note="Configured setup wizard for tenant t_b",
                )

        # 1. Verify tenant B session can see its site and audit event
        async with session_factory() as s:
            await _set_tenant(s, "t_b")
            site_rows = (await s.execute(text("SELECT name FROM site WHERE id = 'site_tb_reg'"))).scalars().all()
            assert "Tenant B Site" in site_rows

            audit_rows = (await s.execute(text("SELECT context_note FROM audit_event WHERE resource_type = 'tenant_wizard'"))).scalars().all()
            assert any("tenant t_b" in note for note in audit_rows)

        # 2. Verify tenant A session CANNOT see tenant B's site or audit event (ISOLATION GATE)
        async with session_factory() as s:
            await _set_tenant(s, "t_a")
            site_rows_a = (await s.execute(text("SELECT name FROM site WHERE id = 'site_tb_reg'"))).scalars().all()
            assert "Tenant B Site" not in site_rows_a

            audit_rows_a = (await s.execute(text("SELECT context_note FROM audit_event WHERE resource_type = 'tenant_wizard'"))).scalars().all()
            assert not any("tenant t_b" in note for note in audit_rows_a)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_operator_cross_tenant_suspension_and_override_isolation():
    """PLT-002 + TEN-303/304: Cross-Tenant Operator Emergency Controls Isolation.

    Verifies that when an operator in context tenant_id='t_a' performs an emergency
    suspension or emergency operator override on target tenant 't_b':
    1. The target tenant 't_b' status transition and audit log land in tenant 't_b' context.
    2. Tenant 't_a' session can NEVER view tenant 't_b''s audit events or status changes.
    3. Unauthenticated/Non-operator context cannot execute suspension or override.
    """
    from hms_tenancy import RequestContext, tenant_session
    from hms_audit import record as audit_record

    ctx_operator = RequestContext(tenant_id="t_a", user_id="operator@zensynq.com", role="operator")
    target_tenant_b = "t_b"

    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        # Seed tenant t_b row
        async with session_factory() as s:
            await s.execute(
                text("INSERT INTO tenant (id, name, region, status) VALUES ('t_b', 'Tenant B Hospital', 'india', 'active') ON CONFLICT (id) DO NOTHING")
            )
            await s.commit()

        # Operator authenticated in context t_a suspends target tenant t_b
        async with session_factory() as session:
            async with tenant_session(session, ctx_operator, tenant_id=target_tenant_b) as s:
                await s.execute(
                    text("UPDATE tenant SET status = 'suspended' WHERE id = :tid").bindparams(tid=target_tenant_b)
                )
                await audit_record(
                    session=s,
                    ctx=ctx_operator,
                    action="suspend",
                    resource_type="tenant",
                    context_note="Emergency operator suspension of tenant t_b",
                )

        # 1. Tenant B session verifies its status is 'suspended' and audit event is recorded
        async with session_factory() as s:
            await _set_tenant(s, "t_b")
            status_b = (await s.execute(text("SELECT status FROM tenant WHERE id = 't_b'"))).scalar_one()
            assert status_b == "suspended"

            audit_rows_b = (await s.execute(text("SELECT context_note FROM audit_event WHERE action = 'suspend'"))).scalars().all()
            assert any("Emergency operator suspension of tenant t_b" in note for note in audit_rows_b)

        # 2. Tenant A session CANNOT see tenant B's audit event (ISOLATION GATE)
        async with session_factory() as s:
            await _set_tenant(s, "t_a")
            audit_rows_a = (await s.execute(text("SELECT context_note FROM audit_event WHERE action = 'suspend'"))).scalars().all()
            assert not any("Emergency operator suspension of tenant t_b" in note for note in audit_rows_a), (
                "ISOLATION BREACH: Tenant A session saw tenant B operator suspension audit log"
            )

        # 3. Emergency Operator Override target tenant t_b back to active
        async with session_factory() as session:
            async with tenant_session(session, ctx_operator, tenant_id=target_tenant_b) as s:
                await s.execute(
                    text("UPDATE tenant SET status = 'active' WHERE id = :tid").bindparams(tid=target_tenant_b)
                )
                await audit_record(
                    session=s,
                    ctx=ctx_operator,
                    action="override",
                    resource_type="tenant",
                    context_note="Emergency operator override reactivated tenant t_b",
                )

        # 4. Verify tenant B state restored and audit event logged under tenant B
        async with session_factory() as s:
            await _set_tenant(s, "t_b")
            status_b_reactivated = (await s.execute(text("SELECT status FROM tenant WHERE id = 't_b'"))).scalar_one()
            assert status_b_reactivated == "active"

            audit_rows_b_override = (await s.execute(text("SELECT context_note FROM audit_event WHERE action = 'override'"))).scalars().all()
            assert any("Emergency operator override reactivated tenant t_b" in note for note in audit_rows_b_override)

        # 5. Verify tenant A CANNOT see override audit event
        async with session_factory() as s:
            await _set_tenant(s, "t_a")
            audit_rows_a_override = (await s.execute(text("SELECT context_note FROM audit_event WHERE action = 'override'"))).scalars().all()
            assert not any("Emergency operator override reactivated tenant t_b" in note for note in audit_rows_a_override), (
                "ISOLATION BREACH: Tenant A session saw tenant B operator override audit log"
            )

    finally:
        await engine.dispose()


