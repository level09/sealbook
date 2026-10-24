#!/bin/sh
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=app_password="$DB_PASSWORD" --set=ON_ERROR_STOP=1 <<'SQL'
CREATE USER stk PASSWORD :'app_password';
ALTER DATABASE stk OWNER TO stk;
SQL
# The app role is not a superuser, so the extensions its migrations need are made here.
psql --username "$POSTGRES_USER" --dbname stk --set=ON_ERROR_STOP=1 <<'SQL'
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
SQL
