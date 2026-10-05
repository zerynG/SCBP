import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


DEBUG = False
ALLOWED_HOSTS = ['127.0.0.1', 'scbp.iqnix.tech', '85.234.110.140']


CSRF_TRUSTED_ORIGINS = [
    'https://scbp.iqnix.tech',
]

# CORS настройки
CORS_ALLOWED_ORIGINS = [
      'https://scbp.iqnix.tech',
]
CORS_ALLOW_CREDENTIALS = True

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Настройки безопасности для HTTPS
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': 'iqnix_scbp',
        'USER': 'us_scbp',
        'PASSWORD': os.environ.get('DB_PASSWORD', '3ec33e4f-ab61-42a6-9467-c1aad7092123'),
        'HOST': 'localhost',
        'PORT': '5432',
    }
}

STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = [
    BASE_DIR / "static",
]


# sudo -u postgres psql
# CREATE DATABASE cop_upk;
# CREATE USER us_upk WITH PASSWORD '3ec33e4f-ab61-42a6-9467-c1aad7092123';
# ALTER ROLE us_scbp SET client_encoding TO 'utf8';
# ALTER ROLE us_scbp SET default_transaction_isolation TO 'read committed';
# ALTER ROLE us_scbp SET timezone TO 'Europe/Moscow';
# GRANT ALL PRIVILEGES ON DATABASE cop_upk TO us_upk;
# \q
