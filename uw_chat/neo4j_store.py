"""Neo4j access for the ADK knowledge agent.

The OKF wiki is loaded by `python3 -m graph_rag.load` into Docker Neo4j.
expand_graph uses this for a first-turn account neighborhood. If Bolt is
down, graph_tool falls back to uw_chat/graph.json.
"""

from __future__ import annotations

import os
from functools import lru_cache

URI = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PASSWORD = os.environ.get("NEO4J_PASSWORD", "llmwiki1")

_DATASTORE = {
    "Account": "accounts",
    "Guideline": "guidelines",
    "Location": "exposures",
    "Accumulation": "exposures",
}

ACCOUNT_NEIGHBORHOOD = """
MATCH (acct:Account {id: $id})
OPTIONAL MATCH (acct)-[:HAS_LOCATION]->(loc:Location)
OPTIONAL MATCH (acct)-[:GOVERNED_BY]->(ag:Guideline)
OPTIONAL MATCH (acct)-[:COUNTS_TOWARD]->(aacc:Accumulation)
OPTIONAL MATCH (loc)-[:GOVERNED_BY]->(lg:Guideline)
OPTIONAL MATCH (loc)-[:COUNTS_TOWARD]->(lacc:Accumulation)
OPTIONAL MATCH (aacc)-[:CAPPED_BY]->(acap:Guideline)
OPTIONAL MATCH (lacc)-[:CAPPED_BY]->(lcap:Guideline)
OPTIONAL MATCH (acct)-[:HAS_DECISION]->(trace:DecisionTrace)
OPTIONAL MATCH (acct)-[:IN_COMMUNITY]->(comm:Community)
OPTIONAL MATCH (comm)-[:PARENT_COMMUNITY]->(book:Community)
OPTIONAL MATCH (acct)-[:FROM_DOCUMENT]->(doc:Document)
WITH acct,
     collect(DISTINCT loc) AS locs,
     collect(DISTINCT ag) AS ags,
     collect(DISTINCT aacc) AS aaccs,
     collect(DISTINCT lg) AS lgs,
     collect(DISTINCT lacc) AS laccs,
     collect(DISTINCT acap) AS acaps,
     collect(DISTINCT lcap) AS lcaps,
     collect(DISTINCT trace) AS traces,
     collect(DISTINCT comm) AS comms,
     collect(DISTINCT book) AS books,
     collect(DISTINCT doc) AS docs
WITH [acct] +
     [n IN locs WHERE n IS NOT NULL] +
     [n IN ags WHERE n IS NOT NULL] +
     [n IN aaccs WHERE n IS NOT NULL] +
     [n IN lgs WHERE n IS NOT NULL] +
     [n IN laccs WHERE n IS NOT NULL] +
     [n IN acaps WHERE n IS NOT NULL] +
     [n IN lcaps WHERE n IS NOT NULL] +
     [n IN traces WHERE n IS NOT NULL] +
     [n IN comms WHERE n IS NOT NULL] +
     [n IN books WHERE n IS NOT NULL] +
     [n IN docs WHERE n IS NOT NULL] AS nodes
UNWIND nodes AS a
UNWIND nodes AS b
OPTIONAL MATCH (a)-[r]->(b)
WHERE type(r) IN [
  'HAS_LOCATION','GOVERNED_BY','COUNTS_TOWARD','CAPPED_BY',
  'HAS_DECISION','ABOUT_ACCOUNT','APPLIED_POLICY','CITED',
  'IN_COMMUNITY','PARENT_COMMUNITY','FROM_DOCUMENT','CONTAINS'
]
WITH nodes, collect(DISTINCT CASE WHEN r IS NULL THEN NULL ELSE {
  source: a.id, relation: type(r), target: b.id
} END) AS raw
RETURN [n IN nodes | {
  id: n.id,
  label: labels(n)[0],
  title: n.name,
  datastore: n.datastore,
  article_id: n.article_id,
  excerpt: n.body,
  level: n.level
}] AS nodes,
[e IN raw WHERE e IS NOT NULL] AS edges
"""

SEARCH = """
MATCH (n)
WHERE n.datastore = $datastore
  AND (
    toLower(n.name) CONTAINS toLower($needle)
    OR toLower(n.body) CONTAINS toLower($needle)
    OR toLower(n.id) CONTAINS toLower($needle)
  )
RETURN n.name AS title, n.body AS content
LIMIT 5
"""


@lru_cache(maxsize=1)
def _driver():
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        URI,
        auth=(USER, PASSWORD),
        connection_timeout=1.5,
        connection_acquisition_timeout=1.5,
    )


def _bolt_host_port() -> tuple[str, int]:
    raw = URI.replace("bolt://", "").replace("neo4j://", "")
    host, _, port = raw.partition(":")
    return host or "127.0.0.1", int(port or "7687")


@lru_cache(maxsize=1)
def is_available() -> bool:
    import socket

    host, port = _bolt_host_port()
    try:
        with socket.create_connection((host, port), timeout=0.2):
            pass
    except OSError:
        return False
    try:
        _driver().verify_connectivity()
        return True
    except Exception:
        return False


def _shape(node: dict) -> dict:
    label = node.get("label") or ""
    return {
        "id": node["id"],
        "label": label,
        "title": node.get("title") or "",
        "datastore": node.get("datastore") or _DATASTORE.get(label, ""),
        "article_id": node.get("article_id") or "",
        "excerpt": (node.get("excerpt") or "")[:2000],
        "level": node.get("level"),
    }


def fetch_account(account_id: str) -> dict:
    with _driver().session() as session:
        rec = session.run(ACCOUNT_NEIGHBORHOOD, id=account_id).single()
    if not rec:
        return {"nodes": [], "edges": []}
    nodes = []
    seen_nodes: set[str] = set()
    for n in rec["nodes"]:
        if not n or not n.get("id") or n["id"] in seen_nodes:
            continue
        seen_nodes.add(n["id"])
        nodes.append(_shape(n))
    seen_edges: set[tuple[str, str, str]] = set()
    edges = []
    for e in rec["edges"]:
        if not e:
            continue
        key = (e["source"], e["relation"], e["target"])
        if key in seen_edges:
            continue
        seen_edges.add(key)
        edges.append(
            {"source": e["source"], "relation": e["relation"], "target": e["target"]}
        )
    return {"nodes": nodes, "edges": edges}


def search_datastore(datastore: str, query: str) -> list[dict]:
    with _driver().session() as session:
        rows = session.run(SEARCH, datastore=datastore, needle=query)
        return [
            {"title": rec["title"] or "", "content": (rec["content"] or "")[:1500]}
            for rec in rows
        ]
