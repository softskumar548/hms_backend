"""mock_db.py — In-memory Mock DB Session for pure unit testing only (lives under tests/).

Must NOT be placed in or imported from app/ runtime code.
"""
from __future__ import annotations

from datetime import datetime
import uuid

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
            {"id": "prereq-1", "tenant_id": "apollo", "code": "FASTING", "description": "Fast for 12 hours before test", "enforcement_type": "hard-stop"},
            {"id": "prereq-2", "tenant_id": "apollo", "code": "CONTRAST_CONSENT", "description": "Contrast injection consent signed", "enforcement_type": "advisory"}
        ],
        "appointment": [],
        "appointment_prerequisite": [],
        "medication_catalog": [],
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
        bind_vals = {}
        if hasattr(statement, "_bindparams"):
            for k, v in statement._bindparams.items():
                val = getattr(v, "value", None)
                if val is None and hasattr(v, "effective_value"):
                    val = getattr(v, "effective_value", None)
                if val is not None:
                    bind_vals[k] = val
        bind_vals.update(kwargs)

        if "set local app.tenant_id" in sql or "app.tenant_id" in sql:
            tid = bind_vals.get("tid") or bind_vals.get("t") or bind_vals.get("tenant_id")
            if not tid and "=" in sql:
                raw_val = sql.split("=")[-1].strip(" ';\"")
                if raw_val and not raw_val.startswith(":"):
                    tid = raw_val
            if tid:
                self.tenant_id = str(tid)
            return MockResult([])

        if "select gen_random_uuid()" in sql:
            return MockResult([{"val": str(uuid.uuid4())}])

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
