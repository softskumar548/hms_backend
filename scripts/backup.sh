#!/usr/bin/env bash
# Off-box encrypted Postgres backup. Schedule via cron on the VPS.
# Even on staging: get the habit right before production. On production this is
# non-negotiable and must target India-region object storage with tested restores.
set -euo pipefail

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="/tmp/hms-${STAMP}.sql.gz"

# Dump (running inside the postgres container of the compose stack).
docker compose exec -T postgres pg_dump -U postgres hms | gzip > "${OUT}"

# Encrypt at rest before it leaves the box (set BACKUP_GPG_RECIPIENT).
if [[ -n "${BACKUP_GPG_RECIPIENT:-}" ]]; then
  gpg --yes --encrypt --recipient "${BACKUP_GPG_RECIPIENT}" "${OUT}"
  rm -f "${OUT}"; OUT="${OUT}.gpg"
fi

# Ship off-box to India-region object storage (configure your provider CLI).
# Example: aws s3 cp "${OUT}" "s3://hms-backups-india/${STAMP}/" --region ap-south-1
echo "backup written: ${OUT} — configure off-box upload for your provider"

# Retention: prune local copies older than 7 days (off-box store keeps the rest).
find /tmp -name 'hms-*.sql.gz*' -mtime +7 -delete || true
