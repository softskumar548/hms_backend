"""Patients router — implementation of the Patient Registration (REG) module.

Enforces:
1. Tenant session isolation & immutable audit logging (PLT-002, PLT-005).
2. OIDC least-privilege role check (receptionist/admin for write).
3. FHIR R4 schemas via fhir.resources Pydantic models (flag F2).
4. Patient duplicate detection algorithm (REG-003).
5. Exposing demographics as FHIR resources & emitting patient.updated event (REG-009).
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fhir.resources.patient import Patient as FHIRPatient
try:
    FHIRPatient.model_rebuild()
except Exception:
    pass
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from hms_audit import record as audit_record
from hms_auth import auth
from hms_events import publish as event_publish
from hms_tenancy import RequestContext, tenant_session

from ..db import get_session
from ..schemas import PatientCreate, PatientOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/patients", tags=["patients"])


async def check_duplicate_patient(
    session: AsyncSession, tenant_id: str, body: PatientCreate
) -> list[dict[str, Any]]:
    """Probabilistic and deterministic duplicate checker (REG-003).
    
    Matches on exact identifiers (deterministic) or computes a match score 
    based on phone, DOB, names, and gender (probabilistic).
    """
    # 1. Deterministic match on exact identifiers (national ID, ABHA, Aarogyasri, PMJAY)
    det_clauses = []
    det_binds = {}
    if body.national_id:
        det_clauses.append("national_id = :national_id")
        det_binds["national_id"] = body.national_id
    if body.abha_number:
        det_clauses.append("abha_number = :abha_number")
        det_binds["abha_number"] = body.abha_number
    if body.aarogyasri_id:
        det_clauses.append("aarogyasri_id = :aarogyasri_id")
        det_binds["aarogyasri_id"] = body.aarogyasri_id
    if body.pmjay_id:
        det_clauses.append("pmjay_id = :pmjay_id")
        det_binds["pmjay_id"] = body.pmjay_id

    if det_clauses:
        sql = f"SELECT id, given_name, family_name, dob, phone FROM patient WHERE {' OR '.join(det_clauses)}"
        rows = (await session.execute(text(sql).bindparams(**det_binds))).mappings().all()
        if rows:
            return [{
                "id": str(r["id"]),
                "given_name": r["given_name"],
                "family_name": r["family_name"],
                "dob": str(r["dob"]) if r["dob"] else None,
                "phone": r["phone"],
                "match_reason": "Deterministic match on patient identifiers",
                "score": 1.0
            } for r in rows]

    # 2. Probabilistic match based on scoring phone, DOB, names, gender
    prob_clauses = []
    prob_binds = {}
    if body.phone:
        prob_clauses.append("phone = :phone")
        prob_binds["phone"] = body.phone
    if body.dob:
        prob_clauses.append("dob = :dob")
        prob_binds["dob"] = body.dob
    if body.given_name:
        prob_clauses.append("LOWER(given_name) = LOWER(:given_name)")
        prob_binds["given_name"] = body.given_name
    if body.family_name:
        prob_clauses.append("LOWER(family_name) = LOWER(:family_name)")
        prob_binds["family_name"] = body.family_name

    if not prob_clauses:
        return []

    sql = f"SELECT id, given_name, family_name, dob, phone, gender FROM patient WHERE {' OR '.join(prob_clauses)}"
    rows = (await session.execute(text(sql).bindparams(**prob_binds))).mappings().all()

    duplicates = []
    for r in rows:
        score = 0.0
        if body.phone and r["phone"] == body.phone:
            score += 0.4
        if body.dob and r["dob"] == body.dob:
            score += 0.3
        if body.given_name and r["given_name"].lower() == body.given_name.lower():
            score += 0.2
        if body.family_name and r["family_name"].lower() == body.family_name.lower():
            score += 0.2
        if body.gender and r["gender"] and r["gender"].lower() == body.gender.lower():
            score += 0.1

        if score >= 0.7:
            duplicates.append({
                "id": str(r["id"]),
                "given_name": r["given_name"],
                "family_name": r["family_name"],
                "dob": str(r["dob"]) if r["dob"] else None,
                "phone": r["phone"],
                "match_reason": f"Probabilistic score of {score:.2f}",
                "score": round(score, 2)
            })

    duplicates.sort(key=lambda x: x["score"], reverse=True)
    return duplicates


def map_to_fhir_patient(patient_id: str, body: PatientCreate) -> dict[str, Any]:
    """Map schemas.PatientCreate fields to a valid FHIR R4 Patient resource representation."""
    fhir_dict: dict[str, Any] = {
        "resourceType": "Patient",
        "id": patient_id,
        "active": True,
        "name": [
            {
                "use": "official",
                "family": body.family_name,
                "given": [body.given_name]
            }
        ],
        "telecom": [],
        "gender": body.gender.lower() if body.gender else None,
        "birthDate": body.dob.isoformat() if body.dob else None,
        "address": [],
        "contact": [],
        "identifier": [],
        "communication": []
    }

    if body.phone:
        fhir_dict["telecom"].append({
            "system": "phone",
            "value": body.phone,
            "use": "mobile"
        })

    if body.email:
        fhir_dict["telecom"].append({
            "system": "email",
            "value": body.email
        })

    if body.preferred_language:
        fhir_dict["communication"].append({
            "language": {
                "coding": [
                    {
                        "system": "urn:ietf:bcp:47",
                        "code": body.preferred_language
                    }
                ]
            },
            "preferred": True
        })

    if body.address and (body.address.get("line1") or body.address.get("city") or body.address.get("postal_code")):
        line_items = [item for item in [body.address.get("line1"), body.address.get("line2")] if item]
        addr_obj: dict[str, Any] = {"use": "home", "type": "postal"}
        if line_items:
            addr_obj["line"] = line_items
        if body.address.get("city"):
            addr_obj["city"] = body.address.get("city")
        if body.address.get("state"):
            addr_obj["state"] = body.address.get("state")
        if body.address.get("postal_code"):
            addr_obj["postalCode"] = body.address.get("postal_code")
        if body.address.get("country"):
            addr_obj["country"] = body.address.get("country")
        fhir_dict["address"].append(addr_obj)

    if body.next_of_kin and body.next_of_kin.get("name"):
        fhir_dict["contact"].append({
            "relationship": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0131",
                            "code": "N"
                        }
                    ],
                    "text": body.next_of_kin.get("relationship") or "Next of Kin"
                }
            ],
            "name": {
                "text": body.next_of_kin.get("name")
            },
            "telecom": [
                {
                    "system": "phone",
                    "value": body.next_of_kin.get("phone")
                }
            ] if body.next_of_kin.get("phone") else []
        })

    # Standard identifiers
    if body.national_id:
        fhir_dict["identifier"].append({
            "use": "official",
            "system": "https://uidai.gov.in",
            "value": body.national_id
        })

    # India ABDM Registry links
    if body.abha_number:
        fhir_dict["identifier"].append({
            "use": "official",
            "system": "https://ndhm.gov.in/abha-number",
            "value": body.abha_number
        })

    if body.abha_address:
        fhir_dict["identifier"].append({
            "use": "official",
            "system": "https://ndhm.gov.in/abha-address",
            "value": body.abha_address
        })

    # Validate against FHIR R4 standard schema using fhir.resources Pydantic models
    validated_patient = FHIRPatient.model_validate(fhir_dict)
    return validated_patient.model_dump(exclude_none=True)


@router.get("", response_model=list[PatientOut])
async def list_patients(
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session),
):
    async with tenant_session(session, ctx) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, given_name, family_name, dob, national_id, phone, "
                    "abha_number, abha_address, aarogyasri_id, pmjay_id, aadhaar_last_four, "
                    "referred_by_type, referred_by_name, referred_by_id, gender, email, "
                    "preferred_language, address, next_of_kin, fhir_resource "
                    "FROM patient ORDER BY family_name, given_name"
                )
            )
        ).mappings().all()
        await audit_record(
            s, ctx, action="read", resource_type="Patient",
            source_ip=request.client.host if request.client else None,
            context_note="list",
        )
        await s.commit()
    
    return [PatientOut(id=str(r["id"]), **{k: r[k] for k in r.keys() if k != "id"}) for r in rows]


@router.post("", response_model=PatientOut, status_code=201)
async def create_patient(
    body: PatientCreate,
    request: Request,
    force: bool = Query(default=False),
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session),
):
    ctx.require_role("admin", "receptionist")  # least-privilege (IAM-002)
    async with tenant_session(session, ctx) as s:
        # 1. Run Duplicate detection check
        if not force:
            duplicates = await check_duplicate_patient(s, ctx.tenant_id, body)
            if duplicates:
                await audit_record(
                    s, ctx, action="read", resource_type="Patient",
                    source_ip=request.client.host if request.client else None,
                    context_note="duplicate detection block triggered",
                )
                await s.commit()
                raise HTTPException(
                    status_code=409,
                    detail={"message": "Duplicate patient detected", "candidates": duplicates}
                )

        # Generate a temporary UUID for the mapping ID
        new_uuid_row = (await s.execute(text("SELECT gen_random_uuid() as val"))).mappings().one()
        patient_id = str(new_uuid_row["val"])

        # 2. Build and Validate FHIR payload
        fhir_resource = map_to_fhir_patient(patient_id, body)

        # 3. Store in hybrid structure
        row = (
            await s.execute(
                text(
                    "INSERT INTO patient "
                    "(id, tenant_id, given_name, family_name, dob, national_id, phone, "
                    "abha_number, abha_address, aarogyasri_id, pmjay_id, aadhaar_last_four, "
                    "referred_by_type, referred_by_name, referred_by_id, gender, email, "
                    "preferred_language, address, next_of_kin, fhir_resource, created_by) "
                    "VALUES (CAST(:id AS uuid), :t, :g, :f, CAST(:d AS date), :n, :p, :abha_num, :abha_addr, :aarogyasri, "
                    ":pmjay, :aadhaar, :ref_type, :ref_name, :ref_id, :gender, :email, "
                    ":language, CAST(:address AS jsonb), CAST(:next_of_kin AS jsonb), CAST(:fhir_resource AS jsonb), :cb) "
                    "RETURNING id, given_name, family_name, dob, national_id, phone, "
                    "abha_number, abha_address, aarogyasri_id, pmjay_id, aadhaar_last_four, "
                    "referred_by_type, referred_by_name, referred_by_id, gender, email, "
                    "preferred_language, address, next_of_kin, fhir_resource"
                ).bindparams(
                    id=patient_id, t=ctx.tenant_id, g=body.given_name, f=body.family_name,
                    d=body.dob, n=body.national_id, p=body.phone, abha_num=body.abha_number,
                    abha_addr=body.abha_address, aarogyasri=body.aarogyasri_id, pmjay=body.pmjay_id,
                    aadhaar=body.aadhaar_last_four, ref_type=body.referred_by_type,
                    ref_name=body.referred_by_name, ref_id=body.referred_by_id, gender=body.gender,
                    email=body.email, language=body.preferred_language,
                    address=json.dumps(body.address.model_dump() if hasattr(body.address, "model_dump") else body.address) if body.address else "{}",
                    next_of_kin=json.dumps(body.next_of_kin.model_dump() if hasattr(body.next_of_kin, "model_dump") else body.next_of_kin) if body.next_of_kin else "{}",
                    fhir_resource=json.dumps(fhir_resource, default=str), cb=ctx.user_id,
                )
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="create", resource_type="Patient",
            resource_id=str(row["id"]), patient_id=str(row["id"]),
            source_ip=request.client.host if request.client else None,
            context_note="force create override" if force else None
        )
        await s.commit()

    # 4. Emit event on event bus (REG-009)
    await event_publish("patient.updated", {
        "id": str(row["id"]),
        "tenant_id": ctx.tenant_id,
        "action": "create",
        "timestamp": fhir_resource.get("meta", {}).get("lastUpdated")
    })

    return PatientOut(id=str(row["id"]), **{k: row[k] for k in row.keys() if k != "id"})


@router.get("/{patient_id}", response_model=PatientOut)
async def get_patient(
    patient_id: str,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session),
):
    async with tenant_session(session, ctx) as s:
        row = (
            await s.execute(
                text(
                    "SELECT id, given_name, family_name, dob, national_id, phone, "
                    "abha_number, abha_address, aarogyasri_id, pmjay_id, aadhaar_last_four, "
                    "referred_by_type, referred_by_name, referred_by_id, gender, email, "
                    "preferred_language, address, next_of_kin, fhir_resource "
                    "FROM patient WHERE id = CAST(:pid AS uuid)"
                ).bindparams(pid=patient_id)
            )
        ).mappings().one_or_none()
        await audit_record(
            s, ctx, action="read", resource_type="Patient",
            resource_id=patient_id, patient_id=patient_id,
            source_ip=request.client.host if request.client else None,
        )
        await s.commit()

    if row is None:
        raise HTTPException(status_code=404, detail="patient not found")

    return PatientOut(id=str(row["id"]), **{k: row[k] for k in row.keys() if k != "id"})


@router.put("/{patient_id}", response_model=PatientOut)
async def update_patient(
    patient_id: str,
    body: PatientCreate,
    request: Request,
    ctx: RequestContext = Depends(auth),
    session: AsyncSession = Depends(get_session),
):
    ctx.require_role("admin", "receptionist")  # least-privilege (IAM-002)
    async with tenant_session(session, ctx) as s:
        # Check if record exists
        existing = (
            await s.execute(text("SELECT id FROM patient WHERE id = CAST(:pid AS uuid)").bindparams(pid=patient_id))
        ).mappings().one_or_none()
        if not existing:
            raise HTTPException(status_code=404, detail="patient not found")

        # Map and validate FHIR payload
        fhir_resource = map_to_fhir_patient(patient_id, body)

        # Update record
        row = (
            await s.execute(
                text(
                    "UPDATE patient SET "
                    "given_name = :g, family_name = :f, dob = :d, national_id = :n, phone = :p, "
                    "abha_number = :abha_num, abha_address = :abha_addr, aarogyasri_id = :aarogyasri, "
                    "pmjay_id = :pmjay, aadhaar_last_four = :aadhaar, referred_by_type = :ref_type, "
                    "referred_by_name = :ref_name, referred_by_id = :ref_id, gender = :gender, "
                    "email = :email, preferred_language = :language, address = :address, "
                    "next_of_kin = :next_of_kin, fhir_resource = :fhir_resource, updated_at = now() "
                    "WHERE id = CAST(:pid AS uuid) "
                    "RETURNING id, given_name, family_name, dob, national_id, phone, "
                    "abha_number, abha_address, aarogyasri_id, pmjay_id, aadhaar_last_four, "
                    "referred_by_type, referred_by_name, referred_by_id, gender, email, "
                    "preferred_language, address, next_of_kin, fhir_resource"
                ).bindparams(
                    pid=patient_id, g=body.given_name, f=body.family_name,
                    d=body.dob, n=body.national_id, p=body.phone, abha_num=body.abha_number,
                    abha_addr=body.abha_address, aarogyasri=body.aarogyasri_id, pmjay=body.pmjay_id,
                    aadhaar=body.aadhaar_last_four, ref_type=body.referred_by_type,
                    ref_name=body.referred_by_name, ref_id=body.referred_by_id, gender=body.gender,
                    email=body.email, language=body.preferred_language,
                    address=body.address, next_of_kin=body.next_of_kin,
                    fhir_resource=fhir_resource,
                )
            )
        ).mappings().one()

        await audit_record(
            s, ctx, action="update", resource_type="Patient",
            resource_id=patient_id, patient_id=patient_id,
            source_ip=request.client.host if request.client else None,
        )
        await s.commit()

    # Emit event (REG-009)
    await event_publish("patient.updated", {
        "id": patient_id,
        "tenant_id": ctx.tenant_id,
        "action": "update",
        "timestamp": fhir_resource.get("meta", {}).get("lastUpdated")
    })

    return PatientOut(id=str(row["id"]), **{k: row[k] for k in row.keys() if k != "id"})

