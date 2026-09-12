"""Load the OKF insurance wiki into Neo4j as a typed Graph RAG store."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adk_underwriting"))
from tools import load_bundle  # noqa: E402

URI = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PASSWORD = os.environ.get("NEO4J_PASSWORD", "llmwiki1")

# Canonical direction only. Reverse markdown links are flipped into these.
REL = {
    ("Account", "Location"): "HAS_LOCATION",
    ("Account", "Guideline"): "GOVERNED_BY",
    ("Location", "Guideline"): "GOVERNED_BY",
    ("Location", "Accumulation"): "COUNTS_TOWARD",
    ("Account", "Accumulation"): "COUNTS_TOWARD",
    ("Accumulation", "Guideline"): "CAPPED_BY",
}


def _label(concept: dict) -> str:
    cid, typ = concept["id"], concept["type"]
    if typ == "Company":
        return "Account"
    if typ == "IKE Guideline":
        return "Guideline"
    if typ == "Exposure" and cid.startswith("exposure/accum"):
        return "Accumulation"
    if typ == "Exposure":
        return "Location"
    return "Page"


def _edge(src_id: str, src_label: str, dst_id: str, dst_label: str):
    rel = REL.get((src_label, dst_label))
    if rel:
        return src_id, src_label, rel, dst_id, dst_label
    rel = REL.get((dst_label, src_label))
    if rel:
        return dst_id, dst_label, rel, src_id, src_label
    return None


def load() -> None:
    bundle = load_bundle()
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
        for label in ("Account", "Guideline", "Location", "Accumulation"):
            session.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")

        for c in bundle.values():
            label = _label(c)
            if label == "Page":
                continue
            session.run(
                f"""
                CREATE (n:{label} {{
                  id: $id,
                  name: $name,
                  type: $type,
                  body: $body,
                  article_id: $article_id
                }})
                """,
                id=c["id"],
                name=c["title"],
                type=c["type"],
                body=c["body"][:4000],
                article_id=c.get("article_id") or "",
            )

        seen: set[tuple[str, str, str]] = set()
        for c in bundle.values():
            src_label = _label(c)
            if src_label == "Page":
                continue
            for dst_id in c["links"]:
                dst = bundle.get(dst_id)
                if not dst:
                    continue
                edge = _edge(c["id"], src_label, dst_id, _label(dst))
                if not edge:
                    continue
                a, a_l, rel, b, b_l = edge
                key = (a, rel, b)
                if key in seen:
                    continue
                seen.add(key)
                session.run(
                    f"""
                    MATCH (a:{a_l} {{id: $a}}), (b:{b_l} {{id: $b}})
                    MERGE (a)-[:{rel}]->(b)
                    """,
                    a=a,
                    b=b,
                )

        label_counts = {
            rec["label"]: rec["n"]
            for rec in session.run(
                "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n"
            )
        }
        rels = session.run("MATCH ()-[r]->() RETURN count(r) AS n").single()["n"]
    driver.close()
    print(
        "loaded Graph RAG store:",
        f"{label_counts.get('Account', 0)} accounts,",
        f"{label_counts.get('Guideline', 0)} guidelines,",
        f"{label_counts.get('Location', 0)} locations,",
        f"{label_counts.get('Accumulation', 0)} accumulations,",
        f"{rels} typed relationships",
    )


if __name__ == "__main__":
    load()
