import os
import sys
import subprocess
import datetime
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DB_USER = os.environ.get("POSTGRES_USER", "postgres")
DB_HOST = os.environ.get("POSTGRES_HOST", "postgres")
DB_PORT = os.environ.get("POSTGRES_PORT", "5432")
DB_NAME = os.environ.get("POSTGRES_DB", "hms")
PASSPHRASE = os.environ.get("BACKUP_PASSPHRASE", "hms_india_vps_backup_secret_2026")

BACKUP_DIR = "/code/backups"
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

def run_backup() -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    enc_file = os.path.join(BACKUP_DIR, f"hms_backup_{ts}.sql.enc")
    
    print(f"[*] Starting encrypted backup: {enc_file}...")
    
    dump_cmd = f"pg_dump -h {DB_HOST} -p {DB_PORT} -U {DB_USER} {DB_NAME}"
    enc_cmd = f"openssl enc -aes-256-cbc -pbkdf2 -pass pass:{PASSPHRASE} -out {enc_file}"
    
    full_cmd = f"{dump_cmd} | {enc_cmd}"
    res = subprocess.run(full_cmd, shell=True, env=dict(os.environ, PGPASSWORD="postgres_change_me"), capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"Backup failed: {res.stderr.decode()}")
    
    size = os.path.getsize(enc_file)
    print(f"[+] Encrypted backup successful: {enc_file} ({size} bytes)")
    return enc_file

def run_restore(enc_file: str):
    print(f"[*] Starting test restore from {enc_file} into '{RESTORE_DB}'...")
    
    # 1. Drop/create restore DB
    drop_cmd = f"psql -h {DB_HOST} -U {DB_USER} -c 'DROP DATABASE IF EXISTS {RESTORE_DB};'"
    create_cmd = f"psql -h {DB_HOST} -U {DB_USER} -c 'CREATE DATABASE {RESTORE_DB};'"
    env = dict(os.environ, PGPASSWORD="postgres_change_me")
    
    subprocess.run(drop_cmd, shell=True, env=env, check=True)
    subprocess.run(create_cmd, shell=True, env=env, check=True)
    
    # 2. Decrypt & pipe into restore DB
    dec_cmd = f"openssl enc -d -aes-256-cbc -pbkdf2 -pass pass:{PASSPHRASE} -in {enc_file}"
    restore_cmd = f"psql -h {DB_HOST} -U {DB_USER} -d {RESTORE_DB}"
    full_cmd = f"{dec_cmd} | {restore_cmd}"
    
    res = subprocess.run(full_cmd, shell=True, env=env, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"Restore failed: {res.stderr.decode()}")
    
    print(f"[+] Decryption and database restoration completed successfully into '{RESTORE_DB}'!")

async def verify_restore():
    orig_url = f"postgresql+asyncpg://{DB_USER}:postgres_change_me@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    rest_url = f"postgresql+asyncpg://{DB_USER}:postgres_change_me@{DB_HOST}:{DB_PORT}/{RESTORE_DB}"
    
    orig_counts = await get_table_counts(orig_url)
    rest_counts = await get_table_counts(rest_url)
    
    print(f"[*] Original DB Counts: {orig_counts}")
    print(f"[*] Restored DB Counts: {rest_counts}")
    
    assert orig_counts == rest_counts, f"Mismatch: {orig_counts} != {rest_counts}"
    print("[+] TEST RESTORE PROVEN SUCCESSFUL: All table counts match 100%!")

async def main():
    enc_file = run_backup()
    run_restore(enc_file)
    await verify_restore()

if __name__ == "__main__":
    asyncio.run(main())
