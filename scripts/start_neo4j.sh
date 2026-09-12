#!/usr/bin/env bash
set -euo pipefail
NEO4J_HOME="${NEO4J_HOME:-$HOME/.local/neo4j/neo4j-community-5.26.30}"
PASSWORD="${NEO4J_PASSWORD:-llmwiki1}"

if [[ ! -x "$NEO4J_HOME/bin/neo4j" ]]; then
  echo "Neo4j not found at $NEO4J_HOME" >&2
  exit 1
fi

conf="$NEO4J_HOME/conf/neo4j.conf"
if grep -q '^#server.default_listen_address=' "$conf"; then
  sed -i 's/^#server.default_listen_address=.*/server.default_listen_address=127.0.0.1/' "$conf"
elif ! grep -q '^server.default_listen_address=' "$conf"; then
  echo 'server.default_listen_address=127.0.0.1' >> "$conf"
fi

if [[ ! -f "$NEO4J_HOME/.password-set" ]]; then
  "$NEO4J_HOME/bin/neo4j-admin" dbms set-initial-password "$PASSWORD"
  touch "$NEO4J_HOME/.password-set"
fi

"$NEO4J_HOME/bin/neo4j" start
echo "Bolt:    bolt://127.0.0.1:7687"
echo "Browser: http://127.0.0.1:7474  (user neo4j / password $PASSWORD)"
echo "Stop with: $NEO4J_HOME/bin/neo4j stop"
