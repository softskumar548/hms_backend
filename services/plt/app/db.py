"""Async SQLAlchemy engine + session factory.

The app connects as the non-superuser role `hms_app` so Row-Level Security is
enforced (superusers bypass RLS). Connection string comes from DATABASE_URL.
"""
from __future__ import annotations

import os
import logging
import uuid
from datetime import datetime, UTC
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

def _load_env_file() -> None:
    import os
    from pathlib import Path
    cur = Path(__file__).resolve().parent
    for parent in [cur] + list(cur.parents):
        env_path = parent / ".env"
        if env_path.is_file():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip())
            except Exception:
                pass
            break

_load_env_file()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://hms_app:app_password_change_me@postgres:5432/hms",
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Mock In-Memory Database Fallback (allows local dev outside Docker)
# ---------------------------------------------------------------------------

class MockMapping:
    def __init__(self, data):
        self._data = data
    def all(self):
        return self._data
    def one(self):
        if not self._data:
            raise KeyError("No row found")
        return self._data[0]
    def one_or_none(self):
        if not self._data:
            return None
        return self._data[0]

class MockResult:
    def __init__(self, data):
        self._data = data
        self.rowcount = len(data)
    def mappings(self):
        return MockMapping(self._data)
    def scalar(self):
        if not self._data:
            return None
        val = list(self._data[0].values())[0]
        return val
    def all(self):
        return [tuple(d.values()) for d in self._data]

class MockAsyncSession:
    # Class-level mock database state to persist across requests/sessions
    _tables = {
        "tenant": [
            {"id": "apollo", "name": "Apollo Clinic (demo)"},
            {"id": "kims", "name": "KIMS Hospital (demo)"}
        ],
        "patient": [
            {
                "id": "c869fbbf-e61e-450e-b7ee-a4cf963a763a",
                "tenant_id": "apollo",
                "given_name": "Venkata",
                "family_name": "Rama Rao",
                "dob": "1972-04-15",
                "national_id": "12-3456-7890",
                "phone": "+919876543210",
                "gender": "male",
                "email": "venkata@example.com",
                "preferred_language": "te",
                "abha_number": "12345678901234",
                "abha_address": "venkata@abdm",
                "aarogyasri_id": "AARO-123",
                "pmjay_id": "PMJAY-456",
                "aadhaar_last_four": "7890",
                "referred_by_type": None,
                "referred_by_name": None,
                "referred_by_id": None,
                "address": None,
                "next_of_kin": None,
                "fhir_resource": None
            },
            {
                "id": "e5c6a8b1-427f-4421-a1e6-b4bc2a983bca",
                "tenant_id": "apollo",
                "given_name": "Sita",
                "family_name": "Devi",
                "dob": "1980-08-20",
                "national_id": None,
                "phone": "+918765432109",
                "gender": "female",
                "email": "sita@example.com",
                "preferred_language": "te",
                "abha_number": None,
                "abha_address": None,
                "aarogyasri_id": None,
                "pmjay_id": None,
                "aadhaar_last_four": None,
                "referred_by_type": None,
                "referred_by_name": None,
                "referred_by_id": None,
                "address": None,
                "next_of_kin": None,
                "fhir_resource": None
            }
        ],
        "site": [
            {"id": "site-1", "tenant_id": "apollo", "name": "Apollo Visakhapatnam"},
            {"id": "site-2", "tenant_id": "kims", "name": "KIMS Secunderabad"}
        ],
        "room": [
            {"id": "room-101", "site_id": "site-1", "tenant_id": "apollo", "name": "Room 101 - Cardiology OPD"},
            {"id": "room-102", "site_id": "site-1", "tenant_id": "apollo", "name": "Room 102 - General OPD"},
            {"id": "room-201", "site_id": "site-2", "tenant_id": "kims", "name": "Room 201 - Cardiology OPD"},
            {"id": "room-202", "site_id": "site-2", "tenant_id": "kims", "name": "Room 202 - General OPD"}
        ],
        "service": [
            {"id": "service-1", "tenant_id": "apollo", "name": "CT Scan Cardiology", "duration_minutes": 30},
            {"id": "service-2", "tenant_id": "apollo", "name": "General Health Checkup", "duration_minutes": 20},
            {"id": "service-1", "tenant_id": "kims", "name": "CT Scan Cardiology", "duration_minutes": 30},
            {"id": "service-2", "tenant_id": "kims", "name": "General Health Checkup", "duration_minutes": 20}
        ],
        "practitioner": [
            {"id": "doc-1", "tenant_id": "apollo", "name": "Dr. Srinivas", "specialism": "Cardiology"},
            {"id": "doc-2", "tenant_id": "apollo", "name": "Dr. Prasad", "specialism": "General Medicine"},
            {"id": "doc-3", "tenant_id": "kims", "name": "Dr. Naidu", "specialism": "Cardiology"},
            {"id": "doc-4", "tenant_id": "kims", "name": "Dr. Rao", "specialism": "General Medicine"}
        ],
        "prerequisite_definition": [
            {"id": "prereq-1", "tenant_id": "apollo", "code": "FASTING", "description": "Fast for 12 hours before test (12 గంటలు ఖాళీ కడుపుతో ఉండాలి)", "enforcement_type": "hard-stop"},
            {"id": "prereq-2", "tenant_id": "apollo", "code": "CONTRAST_CONSENT", "description": "Contrast injection consent signed (ఇంజెక్షన్ సమ్మతి పత్రం)", "enforcement_type": "advisory"},
            {"id": "prereq-1", "tenant_id": "kims", "code": "FASTING", "description": "Fast for 12 hours before test (12 గంటలు ఖాళీ కడుపుతో ఉండాలి)", "enforcement_type": "hard-stop"},
            {"id": "prereq-2", "tenant_id": "kims", "code": "CONTRAST_CONSENT", "description": "Contrast injection consent signed (ఇంజెక్షన్ సమ్మతి పత్రం)", "enforcement_type": "advisory"}
        ],
        "appointment": [
            {
                "id": "appt-1",
                "patient_id": "c869fbbf-e61e-450e-b7ee-a4cf963a763a",
                "practitioner_id": "doc-1",
                "site_id": "site-1",
                "room_id": "room-101",
                "service_id": "service-1",
                "status": "PENDING",
                "start_time": datetime.now().isoformat(),
                "end_time": datetime.now().isoformat(),
                "referred_by_id": None,
                "referred_by_name": None,
                "tenant_id": "apollo"
            }
        ],
        "appointment_prerequisite": [
            {
                "appointment_id": "appt-1",
                "prerequisite_id": "prereq-1",
                "satisfied": False,
                "satisfied_at": None,
                "satisfied_by": None,
                "tenant_id": "apollo"
            },
            {
                "appointment_id": "appt-1",
                "prerequisite_id": "prereq-2",
                "satisfied": False,
                "satisfied_at": None,
                "satisfied_by": None,
                "tenant_id": "apollo"
            }
        ],
        "medication_catalog": [
            {"id": "med-1", "name": "Paracetamol", "generic_name": "Acetaminophen", "form": "tablet", "strength": "500mg", "tenant_id": "apollo"},
            {"id": "med-2", "name": "Amoxicillin", "generic_name": "Amoxicillin", "form": "capsule", "strength": "250mg", "tenant_id": "apollo"},
            {"id": "med-3", "name": "Metformin", "generic_name": "Metformin", "form": "tablet", "strength": "850mg", "tenant_id": "apollo"}
        ],
        "prescription": [],
        "prescription_item": [],
        "patient_consent": [],
        "audit_event": [],
        "encounter": [],
        "clinical_note": [],
        "clinical_note_addendum": [],
        "allergy_intolerance": [],
        "condition": [],
        "medication_statement": [],
        "vital_sign": [],
        "invoice": [],
        "invoice_item": [],
        "payment": [],
        "patient_portal": [],
    }

    def __init__(self):
        self.tenant_id = "apollo"

    async def execute(self, statement, *args, **kwargs):
        sql = str(statement).strip().replace("\n", " ").lower()
        
        # Extract bind params
        bind_vals = {}
        if hasattr(statement, "_bindparams"):
            for k, v in statement._bindparams.items():
                val = getattr(v, "value", None)
                if val is None and hasattr(v, "effective_value"):
                    val = getattr(v, "effective_value", None)
                if val is not None:
                    bind_vals[k] = val
        bind_vals.update(kwargs)

        # 1. SET LOCAL tenant context
        if "set local app.tenant_id" in sql or "app.tenant_id" in sql:
            tid = bind_vals.get("tid") or bind_vals.get("t") or bind_vals.get("tenant_id")
            if not tid and "=" in sql:
                raw_val = sql.split("=")[-1].strip(" ';\"")
                if raw_val and not raw_val.startswith(":"):
                    tid = raw_val
            if tid:
                self.tenant_id = str(tid)
            return MockResult([])

        # 2. gen_random_uuid() (SELECT only)
        if "select gen_random_uuid()" in sql:
            return MockResult([{"val": str(uuid.uuid4())}])

        # 3. Specific join query for clinic queue
        if "from appointment a" in sql and "join patient p" in sql:
            site_id = bind_vals.get("site_id")
            res = []
            for a in self._tables["appointment"]:
                if a.get("site_id") == site_id and a.get("status") in ["ARRIVED", "WAITING", "IN_CONSULTATION", "PENDING"]:
                    patient = next((p for p in self._tables["patient"] if str(p["id"]) == str(a["patient_id"])), {})
                    service = next((s for s in self._tables["service"] if s["id"] == a["service_id"]), {})
                    practitioner = next((doc for doc in self._tables["practitioner"] if doc["id"] == a["practitioner_id"]), {})
                    site = next((st for st in self._tables["site"] if st["id"] == a["site_id"]), {})
                    
                    res.append({
                        "appointment_id": str(a["id"]),
                        "patient_id": str(a["patient_id"]),
                        "patient_name": f"{patient.get('given_name', '')} {patient.get('family_name', '')}".strip(),
                        "status": a["status"],
                        "start_time": a["start_time"],
                        "service_name": service.get("name", "General Service"),
                        "practitioner_name": practitioner.get("name", "Dr. Staff"),
                        "site_name": site.get("name", "General Clinic")
                    })
            return MockResult(res)

        # 4. INSERT INTO
        if "insert into" in sql:
            words = sql.split()
            table_name = None
            for idx, w in enumerate(words):
                if w == "into" and idx + 1 < len(words):
                    table_name = words[idx + 1].strip("(),\"'")
                    break
            
            if table_name and table_name in self._tables:
                tid_val = bind_vals.get("tenant_id") or bind_vals.get("tid") or self.tenant_id
                row_id = bind_vals.get("id") or str(uuid.uuid4())
                new_row = {"id": str(row_id), "tenant_id": str(tid_val)}
                new_row.update(bind_vals)
                new_row["id"] = str(row_id)
                new_row["tenant_id"] = str(tid_val)
                self._tables[table_name].append(new_row)
                return MockResult([new_row])
            return MockResult([])

        # 5. UPDATE
        if "update" in sql:
            words = sql.split()
            table_name = None
            for idx, w in enumerate(words):
                if w == "update" and idx + 1 < len(words):
                    table_name = words[idx + 1].strip("(),\"'")
                    break
            if table_name and table_name in self._tables:
                updated_count = 0
                for r in self._tables[table_name]:
                    match = True
                    for key in ["id", "appointment_id", "patient_id", "practitioner_id"]:
                        if key in bind_vals and key in r:
                            if str(r[key]) != str(bind_vals[key]):
                                match = False
                                break
                    if match:
                        r.update(bind_vals)
                        updated_count += 1
                res_result = MockResult([])
                res_result.rowcount = updated_count
                return res_result

        # 6. SELECT FROM
        if "select" in sql:
            words = sql.split()
            table_name = None
            for idx, w in enumerate(words):
                if w == "from" and idx + 1 < len(words):
                    table_name = words[idx + 1].strip("(),\"'")
                    break
            
            if table_name and table_name in self._tables:
                rows = self._tables[table_name]
                if rows and "tenant_id" in rows[0]:
                    rows = [r for r in rows if r.get("tenant_id") == self.tenant_id]
                
                for k, v in bind_vals.items():
                    if k in ["q"]:
                        continue
                    rows = [r for r in rows if r.get(k) == v or str(r.get(k)) == str(v)]
                
                if "q" in bind_vals and "name" in sql:
                    q_val = str(bind_vals["q"]).replace("%", "").lower()
                    if q_val:
                        rows = [
                            r for r in rows
                            if q_val in str(r.get("name", "")).lower() or q_val in str(r.get("generic_name", "")).lower()
                        ]

                if "count(" in sql:
                    return MockResult([{"count": len(rows)}])

                return MockResult(rows)
            return MockResult([])
            
        return MockResult([])

    def begin(self):
        return self
    async def commit(self):
        pass
    async def rollback(self):
        pass
    async def close(self):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


async def verify_postgres_rls_startup() -> None:
    """Startup check: connect to Postgres, verify 'patient' table RLS (relrowsecurity), refuse to serve otherwise."""
    env_mode = os.getenv("ENV", "development").lower()
    allow_mock = os.getenv("HMS_ALLOW_MOCK_DB", "false").lower() == "true"

    try:
        async with SessionLocal() as session:
            res = (await session.execute(
                text("SELECT relrowsecurity FROM pg_class WHERE relname = 'patient'")
            )).scalar()

            if res is not True:
                raise RuntimeError(
                    "POSTGRES RLS CHECK FAILED: 'patient' table relrowsecurity is False or missing. Refusing to serve requests without active Row-Level Security."
                )
            logger.info("✓ PostgreSQL startup check passed: 'patient' table relrowsecurity is ACTIVE.")
    except Exception as e:
        if env_mode == "test" and allow_mock:
            logger.warning(
                f"⚠️ CRITICAL SECURITY WARNING: Postgres RLS startup check failed ({e}). Mock DB allowed in ENV=test with HMS_ALLOW_MOCK_DB=true."
            )
            return
        logger.error(
            f"FATAL: PostgreSQL RLS startup check failed: {e}. Refusing to start service."
        )
        raise RuntimeError(
            f"PostgreSQL RLS startup check failed: {e}. Refusing to start service without verified RLS database."
        )


async def get_session() -> AsyncSession:
    """FastAPI dependency: yields a session. Hard-gated mock DB fallback."""
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
            yield session
    except Exception as e:
        env_mode = os.getenv("ENV", "development").lower()
        allow_mock = os.getenv("HMS_ALLOW_MOCK_DB", "false").lower() == "true"

        if env_mode == "test" and allow_mock:
            logger.warning(
                "⚠️ CRITICAL SECURITY WARNING: Database connection failed. Running with MockAsyncSession fallback enabled via HMS_ALLOW_MOCK_DB=true in ENV=test!"
            )
            yield MockAsyncSession()
        else:
            logger.error(
                f"FATAL: Database connection failed: {e}. Mock DB fallback is forbidden unless ENV=test and HMS_ALLOW_MOCK_DB=true."
            )
            raise RuntimeError(
                f"Database connection failed: {e}. Live PostgreSQL with Row-Level Security (RLS) is required to serve requests."
            )

