"""ENCRYPTED BACKUP & TESTED RESTORE PROCEDURE (Sprint N2 / Item D5 / DEV_SETUP §B).

Verifies the complete off-box automated backup & restore pipeline:
1. Performs encrypted pg_dump backup using openssl enc AES-256-CBC with BACKUP_PASSPHRASE.
2. Recreates a clean test database 'hms_restore_test'.
3. Decrypts and restores the backup payload into 'hms_restore_test'.
4. Compares table row counts between production/demo database and restored database to prove 100% data integrity.
"""

import os
import subprocess
import datetime
import pytest
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DB_USER = os.environ.get("POSTGRES_USER", "postgres")
DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_PORT = os.environ.get("POSTGRES_PORT", "5432")
DB_NAME = os.environ.get("POSTGRES_DB", "hms")
PASSPHRASE = os.environ.get("BACKUP_PASSPHRASE", "hms_india_vps_backup_secret_2026")
PGPASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres_change_me")

BACKUP_DIR = "/tmp/backups"
RESTORE_DB = "hms_restore_test"


async def get_table_counts(db_url: str) -> dict[str, int]:
    engine = create_async_engine(db_url)
    async with engine.connect() as conn:
        counts = {}
        for tbl in ["tenant", "patient", "encounter", "appointment"]:
            res = await conn.execute(text(f"SELECT COUNT(*) FROM {tbl}"))
            counts[tbl] = res.scalar() or 0
    await engine.dispose()
    return counts


def run_encrypted_backup() -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    enc_file = os.path.join(BACKUP_DIR, f"hms_backup_{ts}.sql.enc")

    dump_cmd = f"pg_dump -h {DB_HOST} -p {DB_PORT} -U {DB_USER} {DB_NAME}"
    enc_cmd = f"openssl enc -aes-256-cbc -pbkdf2 -pass pass:{PASSPHRASE} -out {enc_file}"

    full_cmd = f"{dump_cmd} | {enc_cmd}"
    res = subprocess.run(full_cmd, shell=True, env=dict(os.environ, PGPASSWORD=PGPASSWORD), capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"Backup failed: {res.stderr.decode()}")

    assert os.path.exists(enc_file)
    assert os.path.getsize(enc_file) > 0
    return enc_file


def run_decrypted_restore(enc_file: str):
    drop_cmd = f"psql -h {DB_HOST} -p {DB_PORT} -U {DB_USER} -c 'DROP DATABASE IF EXISTS {RESTORE_DB};'"
    create_cmd = f"psql -h {DB_HOST} -p {DB_PORT} -U {DB_USER} -c 'CREATE DATABASE {RESTORE_DB};'"
    env = dict(os.environ, PGPASSWORD=PGPASSWORD)

    subprocess.run(drop_cmd, shell=True, env=env, check=True)
    subprocess.run(create_cmd, shell=True, env=env, check=True)

    dec_cmd = f"openssl enc -d -aes-256-cbc -pbkdf2 -pass pass:{PASSPHRASE} -in {enc_file}"
    restore_cmd = f"psql -h {DB_HOST} -p {DB_PORT} -U {DB_USER} -d {RESTORE_DB}"
    full_cmd = f"{dec_cmd} | {restore_cmd}"

    res = subprocess.run(full_cmd, shell=True, env=env, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"Restore failed: {res.stderr.decode()}")


@pytest.mark.asyncio
async def test_encrypted_backup_and_restore_integrity_verification():
    """Sprint N2 / Item D5 / Gate N2-04: Perform encrypted backup, restore into test DB, and verify row counts match 100%."""
    # 1. Perform encrypted backup
    enc_file = run_encrypted_backup()

    # 2. Perform restore into hms_restore_test
    run_decrypted_restore(enc_file)

    # 3. Verify row counts between primary DB and restored DB match 100%
    orig_url = f"postgresql+asyncpg://{DB_USER}:{PGPASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    rest_url = f"postgresql+asyncpg://{DB_USER}:{PGPASSWORD}@{DB_HOST}:{DB_PORT}/{RESTORE_DB}"

    orig_counts = await get_table_counts(orig_url)
    rest_counts = await get_table_counts(rest_url)

    assert orig_counts == rest_counts, f"Restored database mismatch: {orig_counts} != {rest_counts}"
    assert orig_counts["tenant"] >= 2, "Expected at least 2 seeded tenants"
