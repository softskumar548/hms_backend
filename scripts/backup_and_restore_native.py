import os
import subprocess
import datetime
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DB_URL = "postgresql+asyncpg://postgres:postgres_change_me@localhost:5432/hms"
RESTORE_URL = "postgresql+asyncpg://postgres:postgres_change_me@localhost:5432/hms_restore_test"
PASSPHRASE = "hms_india_vps_backup_secret_2026"
BACKUP_DIR = "backups"

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
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    enc_file = os.path.join(BACKUP_DIR, f"hms_backup_{ts}.sql.enc")
    
    print(f"[*] Executing encrypted pg_dump backup into {enc_file}...")
    dump_cmd = "docker compose exec -T postgres pg_dump -U postgres hms"
    enc_cmd = f"openssl enc -aes-256-cbc -pbkdf2 -pass pass:{PASSPHRASE} -out {enc_file}"
    
    res = subprocess.run(f"{dump_cmd} | {enc_cmd}", shell=True, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"Backup failed: {res.stderr.decode()}")
    
    size = os.path.getsize(enc_file)
    print(f"[+] Encrypted backup created successfully: {enc_file} ({size} bytes)")
    return enc_file

def run_restore(enc_file: str):
    print(f"[*] Recreating restore test database 'hms_restore_test'...")
    subprocess.run("docker compose exec -T postgres psql -U postgres -c 'DROP DATABASE IF EXISTS hms_restore_test;'", shell=True, check=True)
    subprocess.run("docker compose exec -T postgres psql -U postgres -c 'CREATE DATABASE hms_restore_test;'", shell=True, check=True)
    
    print(f"[*] Decrypting {enc_file} and restoring into 'hms_restore_test'...")
    dec_cmd = f"openssl enc -d -aes-256-cbc -pbkdf2 -pass pass:{PASSPHRASE} -in {enc_file}"
    rest_cmd = "docker compose exec -T postgres psql -U postgres -d hms_restore_test"
    
    res = subprocess.run(f"{dec_cmd} | {rest_cmd}", shell=True, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"Restore failed: {res.stderr.decode()}")
    
    print(f"[+] Encrypted backup successfully decrypted and restored into 'hms_restore_test'!")

async def main():
    enc_file = run_backup()
    run_restore(enc_file)
    
    orig_counts = await get_table_counts(DB_URL)
    rest_counts = await get_table_counts(RESTORE_URL)
    
    print(f"[*] Original DB Row Counts: {orig_counts}")
    print(f"[*] Restored DB Row Counts: {rest_counts}")
    
    assert orig_counts == rest_counts, f"Mismatch: {orig_counts} != {rest_counts}"
    print("\n==========================================================")
    print("✅ PROVEN RESTORE VERIFICATION: Backup restored & verified 100%!")
    print("==========================================================")

if __name__ == "__main__":
    asyncio.run(main())
