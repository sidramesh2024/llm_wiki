"""Graph RAG retrieve: match entities, then expand typed Neo4j patterns."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adk_underwriting"))
from tools import okf_resolve_company  # noqa: E402

URI = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PASSWORD = os.environ.get("NEO4J_PASSWORD", "llmwiki1")

LOCATION_NEEDLES = {
    "houston",
    "dallas",
    "san antonio",
    "secaucus",
    "elk grove",
    "newark",
    "round lake",
    "deerfield",
    "aibonito",
    "bayamón",
    "bayamon",
    "harris",
}

# Start at a Location. Pull the account, guidelines, and accumulation cap.
# This is query-time Graph RAG: Houston can be the start node.
FROM_LOCATION = """
MATCH (loc:Location)
WHERE toLower(loc.name) CONTAINS toLower($needle)
   OR toLower(loc.id) CONTAINS toLower($needle)
OPTIONAL MATCH (loc)<-[:HAS_LOCATION]-(acct:Account)
OPTIONAL MATCH (loc)-[:GOVERNED_BY]->(g:Guideline)
OPTIONAL MATCH (acct)-[:GOVERNED_BY]->(ag:Guideline)
OPTIONAL MATCH (loc)-[:COUNTS_TOWARD]->(acc:Accumulation)
OPTIONAL MATCH (acc)-[:CAPPED_BY]->(cap:Guideline)
WITH loc, acct, acc, cap,
     collect(DISTINCT g) AS loc_guidelines,
     collect(DISTINCT ag) AS acct_guidelines
RETURN loc.name AS location,
       loc.id AS location_id,
       acct.name AS account,
       acct.id AS account_id,
       acc.name AS accumulation,
       acc.id AS accumulation_id,
       cap.name AS cap_guideline,
       cap.id AS cap_id,
       [x IN loc_guidelines WHERE x IS NOT NULL | {name: x.name, id: x.id}] AS guidelines,
       [x IN acct_guidelines WHERE x IS NOT NULL | {name: x.name, id: x.id}] AS account_guidelines
"""

FROM_ACCOUNT = """
MATCH (acct:Account)
WHERE toLower(acct.name) CONTAINS toLower($needle)
   OR toLower(acct.id) CONTAINS toLower($needle)
OPTIONAL MATCH (acct)-[:HAS_LOCATION]->(loc:Location)
OPTIONAL MATCH (acct)-[:GOVERNED_BY]->(g:Guideline)
OPTIONAL MATCH (acct)-[:COUNTS_TOWARD]->(acc:Accumulation)
WITH acct, acc,
     collect(DISTINCT loc) AS locations,
     collect(DISTINCT g) AS guidelines
RETURN acct.name AS account,
       acct.id AS account_id,
       acc.name AS accumulation,
       acc.id AS accumulation_id,
       [x IN locations WHERE x IS NOT NULL | {name: x.name, id: x.id}] AS locations,
       [x IN guidelines WHERE x IS NOT NULL | {name: x.name, id: x.id}] AS guidelines
"""

NODE_TEXT = """
MATCH (n)
WHERE n.id IN $ids
RETURN n.id AS id, labels(n)[0] AS label, n.name AS name, n.body AS body
ORDER BY label, name
"""


def _needles(question: str) -> list[str]:
    q = question.lower()
    known = [
        "texwin",
        "axium",
        "goya",
        "baxter",
        "houston",
        "dallas",
        "san antonio",
        "secaucus",
        "elk grove",
        "newark",
        "round lake",
        "deerfield",
        "aibonito",
        "bayamón",
        "bayamon",
        "harris",
        "puerto rico",
        "flood",
        "warehouse",
    ]
    hits = [k for k in known if k in q]
    specific = [k for k in hits if k not in {"warehouse", "flood"}]
    return specific or hits or re.findall(r"[a-z]{4,}", q)[:4]


def retrieve(question: str) -> dict:
    needles = _needles(question)
    loc_needles = [n for n in needles if n in LOCATION_NEEDLES]
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    rows = []
    mode = "location" if loc_needles else "account"
    start_needles = loc_needles or needles
    cypher = FROM_LOCATION if mode == "location" else FROM_ACCOUNT
    with driver.session() as session:
        for needle in start_needles:
            for rec in session.run(cypher, needle=needle):
                rows.append(dict(rec))
        ids: set[str] = set()
        for row in rows:
            for key in ("location_id", "account_id", "accumulation_id"):
                if row.get(key):
                    ids.add(row[key])
            if row.get("cap_id"):
                ids.add(row["cap_id"])
            for g in row.get("guidelines") or []:
                if g.get("id"):
                    ids.add(g["id"])
            for g in row.get("account_guidelines") or []:
                if g.get("id"):
                    ids.add(g["id"])
            for loc in row.get("locations") or []:
                if loc.get("id"):
                    ids.add(loc["id"])
        chunks = [
            {
                "id": rec["id"],
                "label": rec["label"],
                "name": rec["name"],
                "body": rec["body"],
            }
            for rec in session.run(NODE_TEXT, ids=list(ids))
        ]
    driver.close()
    return {
        "needles": needles,
        "start_needles": start_needles,
        "mode": mode,
        "rows": rows,
        "chunks": chunks,
        "cypher": cypher.strip(),
    }


def format_context(result: dict) -> str:
    lines = [
        f"# Graph RAG context (schema-aware, start={result['mode']})",
        f"Needles in the question: {result['needles']}",
        f"Start needles: {result['start_needles']}",
        "",
        "## Typed paths",
    ]
    seen = set()
    for row in result["rows"]:
        if result["mode"] == "location":
            loc = row.get("location")
            acct = row.get("account")
            if loc and acct:
                seen.add(f"{acct} -[HAS_LOCATION]-> {loc}")
            for g in row.get("guidelines") or []:
                seen.add(f"{loc} -[GOVERNED_BY]-> {g['name']}")
            if acct:
                for g in row.get("account_guidelines") or []:
                    seen.add(f"{acct} -[GOVERNED_BY]-> {g['name']}")
            if row.get("accumulation"):
                seen.add(f"{loc} -[COUNTS_TOWARD]-> {row['accumulation']}")
            if row.get("accumulation") and row.get("cap_guideline"):
                seen.add(f"{row['accumulation']} -[CAPPED_BY]-> {row['cap_guideline']}")
        else:
            acct = row.get("account")
            for loc in row.get("locations") or []:
                seen.add(f"{acct} -[HAS_LOCATION]-> {loc['name']}")
            for g in row.get("guidelines") or []:
                seen.add(f"{acct} -[GOVERNED_BY]-> {g['name']}")
            if row.get("accumulation"):
                seen.add(f"{acct} -[COUNTS_TOWARD]-> {row['accumulation']}")
    if not seen:
        lines.append("(no typed neighbors)")
    else:
        for walk in sorted(seen):
            lines.append(f"- {walk}")
    lines.append("")
    lines.append("## Node text (would go into the LLM)")
    for c in result["chunks"]:
        snippet = " ".join(c["body"].split())[:280]
        lines.append(f"### {c['label']}: {c['name']}")
        lines.append(snippet)
        lines.append("")
    lines.append("## Cypher used")
    lines.append("```")
    lines.append(result["cypher"])
    lines.append("```")
    return "\n".join(lines)


def wiki_side(question: str) -> str:
    resolve = okf_resolve_company(question)
    lines = ["### 1. Pass the whole question to `okf_resolve_company`"]
    if resolve.get("status") != "ok":
        lines.append(f"Miss. `{question}` is not a Company title.")
        lines.append(f"Known accounts: {', '.join(resolve.get('known_companies') or [])}")
    else:
        lines.extend(_wiki_hits(resolve))

    for name in ("Texwin", "Axium", "Goya", "Baxter"):
        if name.lower() in question.lower():
            second = okf_resolve_company(name)
            lines.append("")
            lines.append(f"### 2. Router extracts **{name}**, then hop-1 from that Company page")
            if second.get("status") == "ok":
                lines.extend(_wiki_hits(second))
                lines.append("")
                lines.append(
                    "Hop-1 returns every linked location and IKE article. "
                    "The word Houston does not filter the neighborhood."
                )
            break
    return "\n".join(lines)


def _wiki_hits(resolve: dict) -> list[str]:
    return [
        f"Hit **{resolve['title']}** (`{resolve['company_id']}`)",
        "IKE: " + ", ".join(x["id"] for x in resolve["ike_ids"]),
        "Exposure: " + ", ".join(x["id"] for x in resolve["exposure_ids"]),
        "Snapshot: " + ", ".join(x["id"] for x in resolve["snapshot_ids"]),
    ]


def main(argv: list[str] | None = None) -> None:
    question = " ".join(argv or sys.argv[1:]).strip() or (
        "Can we write Texwin Acquisitions for the Houston warehouse?"
    )
    print(f"Question: {question}\n")
    print("=" * 72)
    print("LLM WIKI (compile-at-ingest, 1 hop from the account page)")
    print("=" * 72)
    print(wiki_side(question))
    print()
    print("=" * 72)
    print("GRAPH RAG (match entities in the question, expand typed Neo4j paths)")
    print("=" * 72)
    print(format_context(retrieve(question)))


if __name__ == "__main__":
    main()
