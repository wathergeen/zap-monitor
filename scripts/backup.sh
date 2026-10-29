#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DB_NAME="${DB_NAME:-zapbot_db}"
DB_USER="${DB_USER:-zapbot_user}"
DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"
BACKUP_FILE="${BACKUP_DIR}/zapbot_${TIMESTAMP}.sql.gz"
ENCRYPTED_FILE="${BACKUP_FILE}.enc"

mkdir -p "$BACKUP_DIR"

echo "[$(date -u +'%Y-%m-%d %H:%M:%SZ')] Iniciando backup do banco de dados ${DB_NAME}..."

# Executa pg_dump comprimido
PGPASSWORD="${DB_PASSWORD:-zapbot_strong_password}" pg_dump \
  -h "$DB_HOST" \
  -p "$DB_PORT" \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  -F c \
  | gzip > "$BACKUP_FILE"

echo "[$(date -u +'%Y-%m-%d %H:%M:%SZ')] Backup gerado em $BACKUP_FILE."

# Criptografia opcional com OpenSSL caso BACKUP_PASSPHRASE esteja definida
if [ -n "${BACKUP_PASSPHRASE:-}" ]; then
  echo "[$(date -u +'%Y-%m-%d %H:%M:%SZ')] Criptografando backup com AES-256-CBC..."
  openssl enc -aes-256-cbc -salt -pbkdf2 -in "$BACKUP_FILE" -out "$ENCRYPTED_FILE" -pass pass:"$BACKUP_PASSPHRASE"
  rm -f "$BACKUP_FILE"
  echo "[$(date -u +'%Y-%m-%d %H:%M:%SZ')] Arquivo criptografado: $ENCRYPTED_FILE"
fi

# Rotação de backups antigos (manter últimos 7 dias)
find "$BACKUP_DIR" -type f \( -name "*.sql.gz" -o -name "*.enc" \) -mtime +7 -delete
echo "[$(date -u +'%Y-%m-%d %H:%M:%SZ')] Rotação concluída com sucesso."

