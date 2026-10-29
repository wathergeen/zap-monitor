#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Uso: $0 <caminho_do_arquivo_de_backup.sql.gz ou .enc>"
  exit 1
fi

BACKUP_FILE="$1"
DB_NAME="${DB_NAME:-zapbot_db}"
DB_USER="${DB_USER:-zapbot_user}"
DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"

TEMP_FILE="/tmp/restore_temp_$$.sql.gz"

trap 'rm -f "$TEMP_FILE"' EXIT

# Se o arquivo estiver criptografado, descriptografar
if [[ "$BACKUP_FILE" == *.enc ]]; then
  if [ -z "${BACKUP_PASSPHRASE:-}" ]; then
    echo "ERRO: O arquivo está criptografado. Defina a variável BACKUP_PASSPHRASE."
    exit 1
  fi
  echo "Descriptografando arquivo de backup..."
  openssl enc -d -aes-256-cbc -pbkdf2 -in "$BACKUP_FILE" -out "$TEMP_FILE" -pass pass:"$BACKUP_PASSPHRASE"
  FILE_TO_RESTORE="$TEMP_FILE"
else
  FILE_TO_RESTORE="$BACKUP_FILE"
fi

echo "Iniciando restauração do banco de dados ${DB_NAME} a partir de ${BACKUP_FILE}..."

# Descompactar e restaurar via pg_restore
PGPASSWORD="${DB_PASSWORD:-zapbot_strong_password}" pg_restore \
  -h "$DB_HOST" \
  -p "$DB_PORT" \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  --clean \
  --if-exists \
  --no-owner \
  "$FILE_TO_RESTORE" || gunzip -c "$FILE_TO_RESTORE" | PGPASSWORD="${DB_PASSWORD:-zapbot_strong_password}" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME"

echo "Restauração do banco de dados concluída com sucesso!"

