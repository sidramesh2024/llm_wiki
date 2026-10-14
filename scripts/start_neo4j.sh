#!/usr/bin/env bash
# Start Neo4j (Docker when the daemon is usable, otherwise the local tarball)
# and load the OKF wiki graph.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASSWORD="${NEO4J_PASSWORD:-llmwiki1}"
export NEO4J_PASSWORD="$PASSWORD"
export NEO4J_URI="${NEO4J_URI:-bolt://127.0.0.1:7687}"
export NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_HOME="${NEO4J_HOME:-$HOME/.local/neo4j/neo4j-community-5.26.30}"

wait_for_bolt() {
  python3 - <<'PY'
import os, time
from neo4j import GraphDatabase
uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
auth = (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "llmwiki1"))
for _ in range(40):
    try:
        driver = GraphDatabase.driver(uri, auth=auth, connection_timeout=2)
        driver.verify_connectivity()
        driver.close()
        raise SystemExit(0)
    except Exception:
        time.sleep(2)
raise SystemExit(1)
PY
}

start_docker() {
  docker compose -f "$ROOT/docker-compose.yml" up -d neo4j
}

start_tarball() {
  if [[ ! -x "$NEO4J_HOME/bin/neo4j" ]]; then
    return 1
  fi
  if [[ ! -f "$NEO4J_HOME/.password-set" ]]; then
    "$NEO4J_HOME/bin/neo4j-admin" dbms set-initial-password "$PASSWORD" || true
    touch "$NEO4J_HOME/.password-set"
  fi
  "$NEO4J_HOME/bin/neo4j" start
}

if docker info >/dev/null 2>&1; then
  echo "Starting Neo4j in Docker..."
  start_docker
  MODE=docker
elif start_tarball; then
  echo "Docker daemon is not usable (add this user to the docker group)."
  echo "Started the local Neo4j tarball instead."
  MODE=tarball
else
  echo "Cannot start Neo4j." >&2
  echo "Grant Docker access, then re-run this script:" >&2
  echo "  sudo usermod -aG docker \$USER && newgrp docker" >&2
  echo "  $ROOT/scripts/start_neo4j.sh" >&2
  exit 1
fi

echo "Waiting for Bolt on 7687..."
if ! wait_for_bolt; then
  echo "Neo4j did not become ready." >&2
  exit 1
fi

python3 -m graph_rag.load
echo "Bolt:    $NEO4J_URI"
echo "Browser: http://127.0.0.1:7474  (user neo4j / password $PASSWORD)"
if [[ "$MODE" == docker ]]; then
  echo "Stop with: docker compose -f $ROOT/docker-compose.yml down"
else
  echo "Stop with: $NEO4J_HOME/bin/neo4j stop"
fi
