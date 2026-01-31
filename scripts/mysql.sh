#!/usr/bin/env bash
set -euo pipefail
source .env

docker exec -it yta-mysql mysql -u"${MYSQL_USER}" -p"${MYSQL_PASSWORD}" "${MYSQL_DATABASE}"
