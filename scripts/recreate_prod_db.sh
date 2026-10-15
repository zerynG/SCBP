#!/usr/bin/env bash
# Пересоздать прод-БД PostgreSQL и инициализировать приложение SCBP.
# Запуск ОТ ROOT на сервере:
#     sudo bash /home/scbp/scripts/recreate_prod_db.sh
#
# ВНИМАНИЕ: безвозвратно удаляет базу iqnix_scbp со всеми данными.
set -euo pipefail

APP_USER=scbp
APP_DIR=/home/scbp
DB_NAME=iqnix_scbp
DB_USER=us_scbp
ADMIN_LOGIN="${SCBP_ADMIN_LOGIN:-admin@iqnix.tech}"
# Пароль НЕ хардкодим в файле. Берём из env SCBP_ADMIN_PASSWORD,
# иначе спрашиваем интерактивно, иначе генерируем случайный.
ADMIN_PASSWORD="${SCBP_ADMIN_PASSWORD:-}"
GENERATED_PW=0

if [ "$(id -u)" -ne 0 ]; then
    echo "Запускать от root: sudo bash $0" >&2
    exit 1
fi

if [ -z "$ADMIN_PASSWORD" ]; then
    if [ -t 0 ]; then
        read -rsp "Пароль для ${ADMIN_LOGIN} (Enter — сгенерировать): " ADMIN_PASSWORD
        echo
    fi
    if [ -z "$ADMIN_PASSWORD" ]; then
        ADMIN_PASSWORD="$(openssl rand -base64 15 2>/dev/null | tr -dc 'A-Za-z0-9' | cut -c1-16)"
        GENERATED_PW=1
    fi
fi

echo "!!! Будет удалена и пересоздана БД ${DB_NAME}. Ctrl-C чтобы отменить..."
sleep 3

# Пароль БД берём из настроек Django (settings_prod или env DB_PASSWORD) —
# чтобы пароль роли гарантированно совпал с тем, что использует приложение.
DB_PASS="$(sudo -u "$APP_USER" -H bash -lc "cd $APP_DIR && source venv/bin/activate && python manage.py shell -c 'from django.conf import settings as s; print(s.DATABASES[\"default\"][\"PASSWORD\"])'" | tail -n 1)"
echo "==> Пароль БД получен из настроек Django (в лог не печатается)"

echo "==> Пересоздаю роль и базу..."
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DROP DATABASE IF EXISTS ${DB_NAME};
DROP ROLE IF EXISTS ${DB_USER};
CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}';
ALTER ROLE ${DB_USER} SET client_encoding TO 'utf8';
ALTER ROLE ${DB_USER} SET default_transaction_isolation TO 'read committed';
ALTER ROLE ${DB_USER} SET timezone TO 'Europe/Moscow';
CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
SQL

echo "==> Миграции..."
sudo -u "$APP_USER" -H bash -lc "cd $APP_DIR && source venv/bin/activate && python manage.py makemigrations --noinput && python manage.py migrate --noinput"

echo "==> Создаю суперпользователя ${ADMIN_LOGIN}..."
sudo -u "$APP_USER" -H bash -lc "cd $APP_DIR && source venv/bin/activate && python manage.py shell" <<PYEOF
from django.contrib.auth import get_user_model
U = get_user_model()
U.objects.filter(username="${ADMIN_LOGIN}").delete()
U.objects.create_superuser("${ADMIN_LOGIN}", "${ADMIN_LOGIN}", "${ADMIN_PASSWORD}")
print("superuser ready:", "${ADMIN_LOGIN}")
PYEOF

echo "==> Рестарт приложения..."
supervisorctl restart "${APP_USER}"
supervisorctl status "${APP_USER}" || true
sleep 2
curl -sS -o /dev/null -w "    HTTP %{http_code}\n" http://127.0.0.1:8000/ || echo "    нет ответа (см. logs/error.log)"

echo "================================================================"
echo "Готово. Вход: https://scbp.iqnix.tech"
echo "  Логин:  ${ADMIN_LOGIN}"
if [ "$GENERATED_PW" = "1" ]; then
    echo "  Пароль (СГЕНЕРИРОВАН): ${ADMIN_PASSWORD}"
    echo "  >> Запиши его сейчас и смени в админке."
else
    echo "  Пароль: (заданный тобой — не печатается)"
fi
echo "================================================================"
