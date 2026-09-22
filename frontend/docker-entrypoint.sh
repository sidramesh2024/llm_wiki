#!/bin/sh
set -eu
API_BASE="${API_BASE:-http://127.0.0.1:8080}"
printf '{"apiBase":"%s"}\n' "$API_BASE" > /usr/share/nginx/html/config.json
exec nginx -g "daemon off;"
