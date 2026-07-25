"""Seed two demo tenants with a realistic synthetic dataset (N1-02).

Run: docker compose exec plt python -m app.seed

SYNTHETIC DATA ONLY — never seed real patient data into dev/staging (CLAUDE.md 3.4).

Per tenant: 2 sites, 4 rooms, 5 practitioners (+ availability), 6 services,
a structured prerequisite library, charge master, medication + lab catalogs,
50 patients (coverage, referrers, ABHA/aadhaar-last-4 fields), appointments
spread past/today/future (incl. DRAFT follow-ups, flag F1), encounters with
notes/vitals/conditions/allergies, signed prescriptions, lab orders/results,
and finalized invoices with payments — so every screen shows real-shaped data
instead of empty states.

Provisioning tenants is a platform-admin action (hms_app has SELECT on `tenant`,
not INSERT — least privilege, PLT-002). Everything else runs as hms_app under
SET LOCAL app.tenant_id, so RLS is exercised end-to-end like a real clinical write.

Deterministic (uuid5 + seeded Random) and idempotent (ON CONFLICT DO NOTHING),
so re-running is safe and diffable.
"""
from __future__ import annotations

import asyncio
import os
import random
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .db import SessionLocal

SEED_DATABASE_URL = os.environ.get(
    "SEED_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres_change_me@postgres:5432/hms",
)

NS = uuid.uuid5(uuid.NAMESPACE_DNS, "hms.zensynq.com/seed")

TENANTS = {"apollo": "Apollo Clinic (demo)", "kims": "KIMS Hospital (demo)"}

GIVEN = ["Ravi", "Priya", "Anil", "Sana", "Lakshmi", "Venkatesh", "Sita", "Ramesh",
         "Divya", "Suresh", "Padma", "Kiran", "Ananya", "Vijay", "Sneha", "Harish",
         "Meena", "Arjun", "Kavya", "Mohan", "Geetha", "Naveen", "Swathi", "Prakash",
         "Bhavani"]
FAMILY = ["Sharma", "Gupta", "Reddy", "Rao", "Naidu", "Chowdary", "Iyer", "Khan",
          "Varma", "Prasad", "Kumar", "Murthy", "Raju", "Goud", "Yadav", "Nair"]

SERVICES = [
    ("svc_gp", "General Consultation", 15),
    ("svc_cardio", "Cardiology Consultation", 30),
    ("svc_usg", "Ultrasound Abdomen", 30),
    ("svc_ct", "CT Scan Cardiology", 45),
    ("svc_lab", "Lab Sample Collection", 10),
    ("svc_fu", "Follow-up Review", 15),
]

# Structured prerequisite library (CLAUDE.md §5 — never free text).
PREREQS = [
    ("prq_fasting", "FASTING_8H", "Fast for 8 hours before the visit", "hard-stop"),
    ("prq_water", "WATER_ONLY", "Only water after midnight", "advisory"),
    ("prq_creat", "CONTRAST_CREATININE", "Recent creatinine result required before contrast", "hard-stop"),
    ("prq_bplog", "BP_LOG_7D", "Bring 7-day home BP log", "advisory"),
    ("prq_labs", "PRIOR_LABS", "Complete ordered labs before review", "advisory"),
    ("prq_bladder", "FULL_BLADDER", "Full bladder for ultrasound", "advisory"),
]

MEDS = [
    ("med_amox", "Amoxicillin", "Amoxicillin", "capsule", "500mg"),
    ("med_metf", "Glycomet", "Metformin", "tablet", "500mg"),
    ("med_amlo", "Amlong", "Amlodipine", "tablet", "5mg"),
    ("med_ator", "Atorva", "Atorvastatin", "tablet", "10mg"),
    ("med_pan", "Pan-40", "Pantoprazole", "tablet", "40mg"),
    ("med_pcm", "Dolo-650", "Paracetamol", "tablet", "650mg"),
    ("med_cetr", "Cetzine", "Cetirizine", "tablet", "10mg"),
    ("med_insulin", "Huminsulin", "Insulin (human)", "injection", "40IU/ml"),
]

LABS = [
    ("lab_cbc", "58410-2", "Complete Blood Count"),
    ("lab_fbs", "1558-6", "Fasting Blood Sugar"),
    ("lab_hba1c", "4548-4", "HbA1c"),
    ("lab_creat", "2160-0", "Serum Creatinine"),
    ("lab_lipid", "57698-3", "Lipid Panel"),
    ("lab_tsh", "3016-3", "TSH"),
]

CHARGES = [
    ("chg_gp", "CON-GP", "General Consultation", "consultation", 300),
    ("chg_cardio", "CON-CAR", "Cardiology Consultation", "consultation", 800),
    ("chg_usg", "IMG-USG", "Ultrasound Abdomen", "imaging", 1200),
    ("chg_ct", "IMG-CT", "CT Scan Cardiology", "imaging", 4500),
    ("chg_cbc", "LAB-CBC", "Complete Blood Count", "laboratory", 350),
    ("chg_fbs", "LAB-FBS", "Fasting Blood Sugar", "laboratory", 150),
    ("chg_fu", "CON-FU", "Follow-up Review", "consultation", 200),
]

CONDITIONS = [
    ("E11.9", "Type 2 diabetes mellitus"),
    ("I10", "Essential hypertension"),
    ("E78.5", "Hyperlipidaemia"),
    ("J45.9", "Asthma"),
    ("K21.9", "GERD"),
]

ALLERGIES = ["Penicillin", "Sulfa drugs", "Ibuprofen", "Shellfish"]


def _uid(*parts: object) -> str:
    return str(uuid.uuid5(NS, ":".join(str(p) for p in parts)))


async def _provision_tenants() -> None:
    admin_engine = create_async_engine(SEED_DATABASE_URL)
    admin_session = async_sessionmaker(admin_engine, expire_on_commit=False)
    async with admin_session() as s:
        for tid, name in TENANTS.items():
            await s.execute(text(
                "INSERT INTO tenant (id, name) VALUES (:t, :n) "
                "ON CONFLICT (id) DO NOTHING").bindparams(t=tid, n=name))
        await s.commit()
    await admin_engine.dispose()


async def _exec(sess, sql: str, **params) -> None:
    await sess.execute(text(sql).bindparams(**params))


async def _seed_tenant(tid: str) -> None:
    rng = random.Random(f"seed-{tid}")
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    async with SessionLocal() as s:  # hms_app role — RLS applies
        await _exec(s, "SELECT set_config('app.tenant_id', :t, true)", t=tid)

        # Clear transactional tables for fresh reproducible state
        for tbl in ["appointment_prerequisite", "payment", "invoice_item", "invoice", 
                    "lab_result", "lab_order_item", "lab_order", "prescription_item", 
                    "prescription", "vital_sign", "clinical_note", "encounter", 
                    "condition", "allergy_intolerance", "patient_consent", 
                    "patient_coverage", "appointment", "patient"]:
            await _exec(s, f"DELETE FROM {tbl} WHERE tenant_id = :t", t=tid)

        # --- Facility & catalogs -------------------------------------------
        sites = [f"site_{tid}_main", f"site_{tid}_annex"]
        await _exec(s, "INSERT INTO site (id, tenant_id, name, address) VALUES "
                       "(:s1, :t, 'Main Campus', 'MG Road, Vijayawada'), "
                       "(:s2, :t, 'Annex Clinic', 'Ring Road, Guntur') "
                       "ON CONFLICT (id) DO NOTHING", s1=sites[0], s2=sites[1], t=tid)
        rooms = []
        for i in range(4):
            rid = f"room_{tid}_{i+1}"
            rooms.append(rid)
            await _exec(s, "INSERT INTO room (id, site_id, tenant_id, name, type) VALUES "
                           "(:r, :s, :t, :n, 'consult') ON CONFLICT (id) DO NOTHING",
                        r=rid, s=sites[i % 2], t=tid, n=f"Room {i+1}")
        docs = []
        for i, (dname, spec) in enumerate([
                ("Dr. Rao", "general-medicine"), ("Dr. Lakshmi", "cardiology"),
                ("Dr. Farida", "radiology"), ("Dr. Venkat", "general-medicine"),
                ("Dr. Sarala", "endocrinology")]):
            did = f"doc_{tid}_{i+1}"
            docs.append(did)
            await _exec(s, "INSERT INTO practitioner (id, tenant_id, name, specialism) VALUES "
                           "(:d, :t, :n, :sp) ON CONFLICT (id) DO NOTHING",
                        d=did, t=tid, n=dname, sp=spec)
            for dow in range(1, 6):  # Mon-Fri
                await _exec(s, "INSERT INTO practitioner_availability "
                               "(id, practitioner_id, site_id, tenant_id, day_of_week, start_time, end_time) "
                               "VALUES (:id, :d, :s, :t, :dow, '09:00', '17:00') "
                               "ON CONFLICT (id) DO NOTHING",
                            id=_uid(tid, "avail", did, dow), d=did, s=sites[i % 2], t=tid,
                            dow=dow)
        for sid, name, mins in SERVICES:
            await _exec(s, "INSERT INTO service (id, tenant_id, name, duration_minutes) VALUES "
                           "(:i, :t, :n, :m) ON CONFLICT (id) DO NOTHING",
                        i=f"{sid}_{tid}", t=tid, n=name, m=mins)
        for pid, code, desc, enforce in PREREQS:
            await _exec(s, "INSERT INTO prerequisite_definition (id, tenant_id, code, description, enforcement_type) "
                           "VALUES (:i, :t, :c, :d, :e) ON CONFLICT (id) DO NOTHING",
                        i=f"{pid}_{tid}", t=tid, c=code, d=desc, e=enforce)
        for mid, name, gen, form, strength in MEDS:
            await _exec(s, "INSERT INTO medication_catalog (id, tenant_id, name, generic_name, form, strength) "
                           "VALUES (:i, :t, :n, :g, :f, :st) ON CONFLICT (id) DO NOTHING",
                        i=f"{mid}_{tid}", t=tid, n=name, g=gen, f=form, st=strength)
            await _exec(s, "INSERT INTO tenant_formulary (id, tenant_id, medication_id) "
                           "VALUES (:i, :t, :m) ON CONFLICT (id) DO NOTHING",
                        i=_uid(tid, "form", mid), t=tid, m=f"{mid}_{tid}")
        for lid, code, name in LABS:
            await _exec(s, "INSERT INTO lab_catalog (id, tenant_id, test_code, name) "
                           "VALUES (:i, :t, :c, :n) ON CONFLICT (id) DO NOTHING",
                        i=f"{lid}_{tid}", t=tid, c=code, n=name)
        for cid, code, name, cat, price in CHARGES:
            await _exec(s, "INSERT INTO charge_master (id, tenant_id, code, name, category, standard_price) "
                           "VALUES (:i, :t, :c, :n, :cat, :p) ON CONFLICT (id) DO NOTHING",
                        i=f"{cid}_{tid}", t=tid, c=code, n=name, cat=cat, p=price)

        # --- Patients -------------------------------------------------------
        # Deterministic e2e anchor (rx-followup flagship): a patient findable by a
        # "Penicillin" name search who carries a high-severity penicillin allergy,
        # so the amoxicillin cross-reactivity alert fires reproducibly.
        anchor_pid = _uid(tid, "patient", "anchor-penicillin")
        await _exec(
            s,
            "INSERT INTO patient (id, tenant_id, given_name, family_name, dob, phone, gender, "
            "email, preferred_language, created_by) "
            "VALUES (CAST(:id AS uuid), :t, 'Penicillin', 'Anchor', '1980-01-01', :ph, 'female', "
            ":em, 'en', 'seed') ON CONFLICT (id) DO NOTHING",
            id=anchor_pid, t=tid, ph="+91-9000000001", em="penicillin.anchor@example.invalid")
        await _exec(
            s,
            "INSERT INTO allergy_intolerance (id, tenant_id, patient_id, substance_code, "
            "substance_display, severity, asserted_at, asserted_by) "
            "VALUES (:id, :t, CAST(:p AS uuid), '91936005', 'Penicillin', 'high', now(), :doc) "
            "ON CONFLICT (id) DO NOTHING",
            id=_uid(tid, "allergy", "anchor-penicillin"), t=tid, p=anchor_pid, doc=docs[0])

        for i in range(50):
            pid = _uid(tid, "patient", i)
            given = GIVEN[i % len(GIVEN)]
            family = FAMILY[(i * 7 + i // len(GIVEN)) % len(FAMILY)]
            dob = date(1950 + rng.randint(0, 55), rng.randint(1, 12), rng.randint(1, 28))
            gender = "female" if i % 2 else "male"
            phone = f"+91-9{rng.randint(100000000, 999999999)}"
            # ~30% carry a referrer (tracking only — commission OFF in India).
            ref_type = ref_name = ref_id = None
            if i % 10 in (0, 3, 6):
                ref_type = rng.choice(["clinician", "clinic", "patient"])
                ref_name = {"clinician": "Dr. Prasad (Guntur)", "clinic": "Sunrise Diagnostics",
                            "patient": "Family referral"}[ref_type]
                ref_id = f"ref_{ref_type}_{i % 4}"
            abha = f"91{rng.randint(10**11, 10**12 - 1)}" if i % 3 == 0 else None  # synthetic 14-digit
            await _exec(
                s,
                "INSERT INTO patient (id, tenant_id, given_name, family_name, dob, phone, gender, "
                "email, preferred_language, abha_number, aadhaar_last_four, "
                "referred_by_type, referred_by_name, referred_by_id, created_by) "
                "VALUES (CAST(:id AS uuid), :t, :g, :f, :dob, :ph, :gen, :em, :lang, :abha, :a4, "
                ":rt, :rn, :ri, 'seed') ON CONFLICT (id) DO NOTHING",
                id=pid, t=tid, g=given, f=family, dob=dob, ph=phone, gen=gender,
                em=f"{given.lower()}.{family.lower()}{i}@example.invalid", lang="te" if i % 2 else "en",
                abha=abha, a4=f"{rng.randint(0, 9999):04d}",
                rt=ref_type, rn=ref_name, ri=ref_id)

            # ~40% have coverage; Aarogyasri is the priority scheme (CLAUDE.md §4).
            cov_id = None
            if i % 5 in (0, 1):
                cov_id = _uid(tid, "cov", i)
                scheme = ["aarogyasri", "pmjay", "private"][i % 3]
                await _exec(
                    s,
                    "INSERT INTO patient_coverage (id, tenant_id, patient_id, scheme_type, plan_name, "
                    "member_id, validity_start, validity_end, patient_share_percent) "
                    "VALUES (:id, :t, CAST(:p AS uuid), :sch, :plan, :mem, '2026-01-01', '2027-12-31', :share) "
                    "ON CONFLICT (id) DO NOTHING",
                    id=cov_id, t=tid, p=pid, sch=scheme,
                    plan={"aarogyasri": "Aarogyasri BPL", "pmjay": "PM-JAY", "private": "Star Health"}[scheme],
                    mem=f"{scheme.upper()[:3]}-{rng.randint(10**7, 10**8 - 1)}",
                    share=0 if scheme in ("aarogyasri", "pmjay") else 20)

            if i % 4 == 0:
                await _exec(s, "INSERT INTO patient_consent (tenant_id, patient_id, purpose) "
                               "VALUES (:t, CAST(:p AS uuid), 'share:abdm')",
                            t=tid, p=pid)
            if i % 8 == 0:
                await _exec(s, "INSERT INTO allergy_intolerance (id, tenant_id, patient_id, substance_code, substance_display, severity, asserted_at, asserted_by) "
                               "VALUES (:id, :t, CAST(:p AS uuid), '91936005', :sub, 'high', now(), :doc) ON CONFLICT (id) DO NOTHING",
                            id=_uid(tid, "allergy", i), t=tid, p=pid,
                            sub=ALLERGIES[i % len(ALLERGIES)], doc=docs[0])
            if i % 3 == 0:
                code, disp = CONDITIONS[i % len(CONDITIONS)]
                await _exec(s, "INSERT INTO condition (id, tenant_id, patient_id, code, display) "
                               "VALUES (:id, :t, CAST(:p AS uuid), :c, :d) ON CONFLICT (id) DO NOTHING",
                            id=_uid(tid, "cond", i), t=tid, p=pid, c=code, d=disp)

            # --- Appointments: one past-completed for most, plus today/future mix.
            doc = docs[i % len(docs)]
            svc_key, _, svc_mins = SERVICES[i % len(SERVICES)]
            svc = f"{svc_key}_{tid}"
            room = rooms[i % len(rooms)]
            site = sites[i % 2]

            past_start = today - timedelta(days=rng.randint(3, 60), hours=-10)
            app_past = _uid(tid, "app-past", i)
            await _exec(
                s,
                "INSERT INTO appointment (id, tenant_id, patient_id, practitioner_id, site_id, room_id, "
                "service_id, status, start_time, end_time, referred_by_id, referred_by_name) "
                "VALUES (:id, :t, CAST(:p AS uuid), :d, :s, :r, :svc, 'COMPLETED', :st, :en, :ri, :rn) "
                "ON CONFLICT (id) DO NOTHING",
                id=app_past, t=tid, p=pid, d=doc, s=site, r=room, svc=svc,
                st=past_start, en=past_start + timedelta(minutes=svc_mins), ri=ref_id, rn=ref_name)

            enc_id = _uid(tid, "enc", i)
            await _exec(
                s,
                "INSERT INTO encounter (id, tenant_id, appointment_id, patient_id, practitioner_id, "
                "site_id, status, signed_at, signed_by) "
                "VALUES (:id, :t, :a, CAST(:p AS uuid), :d, :s, 'signed', :sat, :d) ON CONFLICT (id) DO NOTHING",
                id=enc_id, t=tid, a=app_past, p=pid, d=doc, s=site,
                sat=past_start + timedelta(hours=1))
            await _exec(
                s,
                "INSERT INTO clinical_note (id, tenant_id, encounter_id, template_type, rich_text_content) "
                "VALUES (:id, :t, :e, 'SOAP', :txt) ON CONFLICT (id) DO NOTHING",
                id=_uid(tid, "note", i), t=tid, e=enc_id,
                txt=f"S: routine review. O: stable. A: {CONDITIONS[i % len(CONDITIONS)][1]}. P: continue meds.")
            for vtype, val, unit in [("heart_rate", 68 + i % 30, "bpm"),
                                     ("bp_systolic", 110 + i % 40, "mmHg"),
                                     ("temperature", 36 + (i % 3) * 0.5, "C")]:
                await _exec(s, "INSERT INTO vital_sign (id, tenant_id, encounter_id, patient_id, type, value, unit) "
                               "VALUES (:id, :t, :e, CAST(:p AS uuid), :ty, :v, :u) ON CONFLICT (id) DO NOTHING",
                            id=_uid(tid, "vital", i, vtype), t=tid, e=enc_id, p=pid,
                            ty=vtype, v=val, u=unit)

            # Signed prescription for two-thirds of past encounters.
            if i % 3 != 2:
                rx_id = _uid(tid, "rx", i)
                await _exec(
                    s,
                    "INSERT INTO prescription (id, tenant_id, patient_id, practitioner_id, encounter_id, "
                    "status, signed_at, signed_by) VALUES (:id, :t, CAST(:p AS uuid), :d, :e, 'signed', :sat, :d) "
                    "ON CONFLICT (id) DO NOTHING",
                    id=rx_id, t=tid, p=pid, d=doc, e=enc_id, sat=past_start + timedelta(hours=1))
                for j in range(1 + i % 2):
                    mid = f"{MEDS[(i + j) % len(MEDS)][0]}_{tid}"
                    await _exec(
                        s,
                        "INSERT INTO prescription_item (id, tenant_id, prescription_id, medication_id, "
                        "dose, unit, route, frequency, duration_days, quantity) "
                        "VALUES (:id, :t, :rx, :m, 1, 'tablet', 'oral', 'twice daily', 10, 20) "
                        "ON CONFLICT (id) DO NOTHING",
                        id=_uid(tid, "rxi", i, j), t=tid, rx=rx_id, m=mid)

            # Lab order + result for half.
            if i % 2 == 0:
                ord_id = _uid(tid, "ord", i)
                lab = f"{LABS[i % len(LABS)][0]}_{tid}"
                await _exec(
                    s,
                    "INSERT INTO lab_order (id, tenant_id, patient_id, practitioner_id, encounter_id, status) "
                    "VALUES (:id, :t, CAST(:p AS uuid), :d, :e, 'resulted') ON CONFLICT (id) DO NOTHING",
                    id=ord_id, t=tid, p=pid, d=doc, e=enc_id)
                await _exec(s, "INSERT INTO lab_order_item (id, tenant_id, order_id, test_id) "
                               "VALUES (:id, :t, :o, :l) ON CONFLICT (id) DO NOTHING",
                            id=_uid(tid, "ordi", i), t=tid, o=ord_id, l=lab)
                await _exec(s, "INSERT INTO lab_result (id, tenant_id, test_id, value, unit) "
                               "VALUES (:id, :t, :l, :v, 'mg/dL') ON CONFLICT (id) DO NOTHING",
                            id=_uid(tid, "res", i), t=tid, l=lab, v=80 + i % 60)

            # Finalized invoice + payment for completed visits.
            inv_id = _uid(tid, "inv", i)
            chg_key = CHARGES[i % len(CHARGES)][0]
            price = CHARGES[i % len(CHARGES)][4]
            payer_share = price if (cov_id and i % 3 != 2) else 0
            await _exec(
                s,
                "INSERT INTO invoice (id, tenant_id, patient_id, encounter_id, status, coverage_id, "
                "total_amount, payer_responsibility, patient_responsibility) "
                "VALUES (:id, :t, CAST(:p AS uuid), :e, 'finalized', :c, :tot, :pay, :pat) "
                "ON CONFLICT (id) DO NOTHING",
                id=inv_id, t=tid, p=pid, e=enc_id, c=cov_id,
                tot=price, pay=payer_share, pat=price - payer_share)
            await _exec(
                s,
                "INSERT INTO invoice_item (id, tenant_id, invoice_id, charge_item_id, unit_price, "
                "patient_share, payer_share) VALUES (:id, :t, :inv, :c, :up, :ps, :ys) "
                "ON CONFLICT (id) DO NOTHING",
                id=_uid(tid, "invi", i), t=tid, inv=inv_id, c=f"{chg_key}_{tid}",
                up=price, ps=price - payer_share, ys=payer_share)
            if price - payer_share > 0:
                await _exec(s, "INSERT INTO payment (id, tenant_id, invoice_id, payment_method, amount) "
                               "VALUES (:id, :t, :inv, :m, :amt) ON CONFLICT (id) DO NOTHING",
                            id=_uid(tid, "pay", i), t=tid, inv=inv_id,
                            m=rng.choice(["cash", "upi", "card"]), amt=price - payer_share)

            # Upcoming bookings for ~half; every 5th is a DRAFT follow-up (flag F1)
            # with structured prerequisites attached (CLAUDE.md §5).
            if i % 2 == 0:
                fut_start = today + timedelta(days=rng.randint(0, 14), hours=9 + i % 7)
                status = "DRAFT" if i % 10 == 0 else ("ARRIVED" if i % 10 == 4 else "BOOKED")
                app_fut = _uid(tid, "app-fut", i)
                await _exec(
                    s,
                    "INSERT INTO appointment (id, tenant_id, patient_id, practitioner_id, site_id, room_id, "
                    "service_id, status, start_time, end_time, referred_by_id, referred_by_name) "
                    "VALUES (:id, :t, CAST(:p AS uuid), :d, :s, :r, :svc, :stat, :st, :en, :ri, :rn) "
                    "ON CONFLICT (id) DO NOTHING",
                    id=app_fut, t=tid, p=pid, d=doc, s=site, r=room,
                    svc=f"svc_fu_{tid}" if status == "DRAFT" else svc, stat=status,
                    st=fut_start, en=fut_start + timedelta(minutes=svc_mins), ri=ref_id, rn=ref_name)
                for k, (pq_key, _, _, _) in enumerate(PREREQS[: 2 if i % 4 == 0 else 1]):
                    await _exec(
                        s,
                        "INSERT INTO appointment_prerequisite (appointment_id, prerequisite_id, tenant_id, satisfied) "
                        "VALUES (:a, :pq, :t, :sat) ON CONFLICT DO NOTHING",
                        a=app_fut, pq=f"{pq_key}_{tid}", t=tid, sat=k == 0 and i % 4 != 0)

        await s.commit()


async def seed() -> None:
    await _provision_tenants()
    for tid in TENANTS:
        await _seed_tenant(tid)
    print("seeded tenants: apollo, kims — 50 synthetic patients each with "
          "schedules, encounters, prescriptions, orders, invoices, referrers, prereq library")


if __name__ == "__main__":
    asyncio.run(seed())
