#!/usr/bin/env bash
# deploy.sh — обновление SCBP на сервере.
# Запуск ОТ ROOT:  sudo bash /home/scbp/deploy.sh
# Шаги с кодом/БД выполняются под пользователем scbp, рестарт supervisor — под root.
set -euo pipefail

APP_USER=scbp
APP_DIR=/home/scbp
SERVICE=scbp

if [ "$(id -u)" -ne 0 ]; then
    echo "Запускать от root: sudo bash $0" >&2
    exit 1
fi

mkdir -p "$APP_DIR/logs"
exec > >(tee -a "$APP_DIR/logs/deploy.log") 2>&1
TS() { date '+%Y-%m-%d %H:%M:%S'; }
echo ""
echo "==================== DEPLOY START $(TS) ===================="

# Выравниваем владельца (git/файлы должны принадлежать scbp, иначе reset падает)
chown -R "$APP_USER:$APP_USER" "$APP_DIR" 2>/dev/null || true

# ----------------------------------------------------------------------------
# Шаги под пользователем приложения (scbp): git, зависимости, БД, статика.
# ----------------------------------------------------------------------------
sudo -u "$APP_USER" -H bash <<'SCBP_STEPS'
set -euo pipefail
cd /home/scbp
git config --global --add safe.directory /home/scbp 2>/dev/null || true

GIT_REMOTE_URL="https://git.nik-a.tech/chumakov/SCBP.git"
[ "$(git remote get-url origin 2>/dev/null || echo)" = "$GIT_REMOTE_URL" ] || git remote set-url origin "$GIT_REMOTE_URL"

mkdir -p logs .deploy cache

echo "[1/6] git fetch + reset --hard origin/main"
git fetch --all --prune
git reset --hard origin/main

echo "[2/6] activate venv"
if [ -f /home/scbp/venv/bin/activate ]; then source /home/scbp/venv/bin/activate
elif [ -f ./venv/bin/activate ]; then source ./venv/bin/activate
else echo "ERROR: venv не найден" >&2; exit 1; fi

echo "[3/6] pip install (если requirements.txt изменился)"
REQ_HASH_FILE=.deploy/reqs.sha256
CUR_HASH="$(sha256sum requirements.txt | awk '{print $1}')"
OLD_HASH="$(cat "$REQ_HASH_FILE" 2>/dev/null || echo)"
if [ "$CUR_HASH" != "$OLD_HASH" ]; then
    pip install --upgrade pip && pip install -r requirements.txt && echo "$CUR_HASH" > "$REQ_HASH_FILE"
else
    echo "    без изменений → пропуск"
fi

echo "[4/6] backup БД (pg_dump, только PostgreSQL)"
# Параметры берём из django settings через manage.py (корректно подставляет
# DJANGO_SETTINGS_MODULE), а не голым python — иначе движок не определяется.
DB_PARAMS="$(python manage.py shell -c "from django.conf import settings as s; d=s.DATABASES['default']; print('|'.join([str(d.get('ENGINE','')), str(d.get('NAME','')), str(d.get('USER','')), str(d.get('HOST','localhost')), str(d.get('PORT','5432')), str(d.get('PASSWORD',''))]))" 2>/dev/null | tail -n 1)"
IFS='|' read -r DB_ENGINE DB_NAME DB_USER DB_HOST DB_PORT DB_PASSWORD <<< "$DB_PARAMS"
if [[ "$DB_ENGINE" == *postgresql* ]]; then
    mkdir -p /home/scbp/backups
    F="/home/scbp/backups/${DB_NAME}-pre-deploy-$(date +%Y%m%d-%H%M%S).sql.gz"
    if PGPASSWORD="$DB_PASSWORD" pg_dump -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" --no-owner --no-privileges "$DB_NAME" 2>/tmp/pgdump.err | gzip -9 > "$F"; then
        echo "    backup OK: $F ($(du -h "$F" | cut -f1))"
        ls -1t "/home/scbp/backups/${DB_NAME}-pre-deploy-"*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
    else
        echo "    ВНИМАНИЕ: pg_dump не удался (новая/пустая БД?) — продолжаю без бэкапа"
        rm -f "$F"
    fi
else
    echo "    БД не PostgreSQL ($DB_ENGINE) → бэкап пропущен"
fi

echo "[5/6] makemigrations + migrate"
python manage.py makemigrations --noinput
python manage.py migrate --noinput

echo "[6/6] collectstatic"
python manage.py collectstatic --noinput
SCBP_STEPS

# ----------------------------------------------------------------------------
# Рестарт supervisor — под root (scbp не нужен beзпарольный sudo).
# ----------------------------------------------------------------------------
echo "[restart] supervisorctl restart $SERVICE"
supervisorctl restart "$SERVICE"
supervisorctl status "$SERVICE" || true

echo "[health] http://127.0.0.1:8000/"
sleep 2
curl -fsS -o /dev/null -w "    HTTP %{http_code}\n" http://127.0.0.1:8000/ || echo "    нет ответа (см. logs/error.log)"

echo "==================== DEPLOY OK $(TS) ===================="
