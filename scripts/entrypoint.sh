#!/usr/bin/env bash
set -e

# Aguardar banco de dados se configurado
if [ -n "$DB_HOST" ]; then
  echo "Aguardando banco de dados em $DB_HOST:$DB_PORT..."
  while ! python -c "
import socket, sys
try:
    s = socket.create_connection(('$DB_HOST', int('$DB_PORT')), timeout=2)
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
"; do
    sleep 1
  done
  echo "Banco de dados disponível!"
fi

MODE="${1:-web}"

case "$MODE" in
  web)
    echo "Executando migrações do banco de dados..."
    python manage.py migrate --noinput

    echo "Coletando arquivos estáticos..."
    python manage.py collectstatic --noinput

    echo "Iniciando servidor Gunicorn (gthread: 2 workers × 4 threads)..."
    exec gunicorn core.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers 2 \
      --threads 4 \
      --timeout 60 \
      --max-requests 1000 \
      --max-requests-jitter 100 \
      --worker-tmp-dir /dev/shm \
      --access-logfile - \
      --error-logfile -
    ;;

  worker)
    echo "Iniciando Procrastinate Worker (PostgreSQL LISTEN/NOTIFY)..."
    exec python manage.py procrastinate worker --concurrency "${WORKER_CONCURRENCY:-4}"
    ;;

  migrate)
    echo "Executando migrações do banco de dados e schema do Procrastinate..."
    exec python manage.py migrate --noinput
    ;;

  *)
    exec "$@"
    ;;
esac
