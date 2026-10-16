"""Hierarchical knowledge layer on top of the typed UW entities.

Mirrors Neo4j's knowledge-layer stack (source docs → knowledge graph →
decision traces) and GraphRAG community levels (2 = book, 1 = theme).

https://neo4j.com/blog/agentic-ai/knowledge-layer/
"""

from __future__ import annotations

import re

# Theme communities (level 1) sit under the property book (level 2).
COMMUNITIES: dict[str, dict] = {
    "comm/l2/property-book": {
        "name": "Commercial property book",
        "level": 2,
        "parent": None,
        "members": [],
    },
    "comm/l1/food-processing": {
        "name": "Food processing",
        "level": 1,
        "parent": "comm/l2/property-book",
        "members": [
            "companies/axium-foods",
            "exposure/axium-elk-grove-plant",
            "exposure/axium-newark-dc",
            "ike/occupancy-food-processing",
            "ike/hazard-cooking-fryer",
            "ike/hazard-cold-storage",
            "ike/product-contamination-recall",
        ],
    },
    "comm/l1/life-sciences": {
        "name": "Life sciences",
        "level": 1,
        "parent": "comm/l2/property-book",
        "members": [
            "companies/baxter-international",
            "exposure/baxter-round-lake",
            "exposure/baxter-deerfield",
            "exposure/baxter-aibonito-pr",
            "ike/occupancy-life-sciences",
        ],
    },
    "comm/l1/food-distribution": {
        "name": "Food distribution",
        "level": 1,
        "parent": "comm/l2/property-book",
        "members": [
            "companies/goya-foods",
            "exposure/goya-secaucus",
            "exposure/goya-bayamon-pr",
            "exposure/goya-san-antonio-dc",
        ],
    },
    "comm/l1/warehouse-acquisition": {
        "name": "Warehouse acquisition",
        "level": 1,
        "parent": "comm/l2/property-book",
        "members": [
            "companies/texwin-acquisitions",
            "exposure/texwin-houston-whse",
            "exposure/texwin-dallas-whse",
            "exposure/texwin-san-antonio-whse",
            "ike/thin-named-insured",
        ],
    },
    "comm/l1/puerto-rico-cat": {
        "name": "Puerto Rico CAT",
        "level": 1,
        "parent": "comm/l2/property-book",
        "members": [
            "companies/baxter-international",
            "companies/goya-foods",
            "exposure/baxter-aibonito-pr",
            "exposure/goya-bayamon-pr",
            "exposure/accum-puerto-rico",
            "ike/puerto-rico-cat",
        ],
    },
    "comm/l1/harris-county": {
        "name": "Harris County accumulation",
        "level": 1,
        "parent": "comm/l2/property-book",
        "members": [
            "companies/texwin-acquisitions",
            "exposure/texwin-houston-whse",
            "exposure/accum-harris-county",
            "ike/harris-county-accumulation",
            "ike/flood-zone-ae",
        ],
    },
    "comm/l1/warehouse": {
        "name": "Warehouse occupancy",
        "level": 1,
        "parent": "comm/l2/property-book",
        "members": [
            "companies/goya-foods",
            "companies/texwin-acquisitions",
            "ike/occupancy-warehouse",
            "exposure/goya-secaucus",
            "exposure/goya-san-antonio-dc",
            "exposure/texwin-houston-whse",
            "exposure/texwin-dallas-whse",
            "exposure/texwin-san-antonio-whse",
        ],
    },
    "comm/l1/coastal": {
        "name": "Coastal named storm",
        "level": 1,
        "parent": "comm/l2/property-book",
        "members": [
            "exposure/axium-newark-dc",
            "exposure/goya-secaucus",
            "ike/coastal-named-storm",
        ],
    },
}

_HEADING = re.compile(r"(?m)^## .+")


def _chunks(concept_id: str, body: str) -> list[tuple[str, str, str]]:
    """Split a page on ## headings into ordered chunks."""
    text = (body or "").strip()
    if not text:
        return []
    parts = _HEADING.split(text)
    heads = _HEADING.findall(text)
    out: list[tuple[str, str, str]] = []
    lead = parts[0].strip() if parts else ""
    if lead:
        out.append((f"chunk:{concept_id}:0", "Overview", lead[:2000]))
    for i, heading in enumerate(heads, start=1):
        title = heading.lstrip("# ").strip()
        block = parts[i].strip() if i < len(parts) else ""
        if not block:
            continue
        out.append((f"chunk:{concept_id}:{i}", title, block[:2000]))
    return out[:8]


def attach(session, bundle: dict[str, dict], label_of) -> dict[str, int]:
    """Write documents, chunks, communities, decision traces, and CONTAINS tree."""
    for extra in ("Document", "Chunk", "Community", "DecisionTrace", "KnowledgeLayer"):
        session.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{extra}) REQUIRE n.id IS UNIQUE")

    session.run(
        """
        MERGE (k:KnowledgeLayer {id: 'layer/uw-desk'})
        SET k.name = 'Commercial property underwriting',
            k.level = 3
        MERGE (src:Layer {id: 'layer/source', level: 0})
        SET src.name = 'Source documents'
        MERGE (kg:Layer {id: 'layer/knowledge', level: 1})
        SET kg.name = 'Knowledge graph'
        MERGE (dec:Layer {id: 'layer/decision', level: 2})
        SET dec.name = 'Decision traces'
        MERGE (k)-[:CONTAINS]->(src)
        MERGE (k)-[:CONTAINS]->(kg)
        MERGE (k)-[:CONTAINS]->(dec)
        """
    )

    docs = chunks = 0
    for concept in bundle.values():
        label = label_of(concept)
        doc_id = f"doc:{concept['id']}"
        datastore = {
            "Account": "accounts",
            "Guideline": "guidelines",
            "Location": "exposures",
            "Accumulation": "exposures",
            "Page": "syntheses",
        }.get(label, "")
        if concept["type"] == "UW Snapshot":
            datastore = "syntheses"
        session.run(
            """
            MERGE (d:Document {id: $id})
            SET d.name = $name, d.path = $path, d.body = $body,
                d.datastore = $datastore, d.level = 0
            WITH d
            MATCH (src:Layer {id: 'layer/source'})
            MERGE (d)-[:IN_LAYER]->(src)
            """,
            id=doc_id,
            name=concept["title"],
            path=concept.get("path") or "",
            body=concept["body"][:4000],
            datastore=datastore,
        )
        docs += 1
        prev = None
        for chunk_id, title, text in _chunks(concept["id"], concept["body"]):
            session.run(
                """
                MATCH (d:Document {id: $doc})
                MERGE (c:Chunk {id: $id})
                SET c.name = $title, c.body = $body, c.level = 0
                MERGE (d)-[:HAS_CHUNK]->(c)
                MERGE (d)-[:CONTAINS]->(c)
                """,
                doc=doc_id,
                id=chunk_id,
                title=title,
                body=text,
            )
            if prev:
                session.run(
                    """
                    MATCH (a:Chunk {id: $a}), (b:Chunk {id: $b})
                    MERGE (a)-[:NEXT_CHUNK]->(b)
                    """,
                    a=prev,
                    b=chunk_id,
                )
            prev = chunk_id
            chunks += 1
        if label != "Page":
            session.run(
                """
                MATCH (n {id: $eid}), (d:Document {id: $did})
                MERGE (n)-[:FROM_DOCUMENT]->(d)
                """,
                eid=concept["id"],
                did=doc_id,
            )
            session.run(
                """
                MATCH (n {id: $eid})-[:FROM_DOCUMENT]->(d:Document)-[:HAS_CHUNK]->(c:Chunk)
                MERGE (c)-[:MENTIONS]->(n)
                """,
                eid=concept["id"],
            )

    session.run(
        """
        MATCH (kg:Layer {id: 'layer/knowledge'})
        MATCH (n)
        WHERE n:Account OR n:Location OR n:Guideline OR n:Accumulation
        SET n.level = 1
        MERGE (n)-[:IN_LAYER]->(kg)
        """
    )

    for cid, meta in COMMUNITIES.items():
        session.run(
            """
            MERGE (c:Community {id: $id})
            SET c.name = $name, c.level = $level
            """,
            id=cid,
            name=meta["name"],
            level=meta["level"],
        )
    for cid, meta in COMMUNITIES.items():
        parent = meta.get("parent")
        if parent:
            session.run(
                """
                MATCH (c:Community {id: $id}), (p:Community {id: $parent})
                MERGE (c)-[:PARENT_COMMUNITY]->(p)
                MERGE (p)-[:CONTAINS]->(c)
                """,
                id=cid,
                parent=parent,
            )
        for member in meta.get("members") or []:
            session.run(
                """
                MATCH (c:Community {id: $cid}), (n {id: $nid})
                MERGE (n)-[:IN_COMMUNITY]->(c)
                MERGE (c)-[:CONTAINS]->(n)
                """,
                cid=cid,
                nid=member,
            )

    session.run(
        """
        MATCH (k:KnowledgeLayer {id: 'layer/uw-desk'}), (book:Community {id: 'comm/l2/property-book'})
        MERGE (k)-[:CONTAINS]->(book)
        """
    )

    traces = 0
    for concept in bundle.values():
        if concept["type"] != "UW Snapshot":
            continue
        session.run(
            """
            MERGE (t:DecisionTrace {id: $id})
            SET t.name = $name, t.body = $body, t.level = 2, t.datastore = 'syntheses'
            WITH t
            MATCH (dec:Layer {id: 'layer/decision'})
            MERGE (t)-[:IN_LAYER]->(dec)
            WITH t
            MATCH (d:Document {id: $doc})
            MERGE (t)-[:FROM_DOCUMENT]->(d)
            """,
            id=concept["id"],
            name=concept["title"],
            body=concept["body"][:4000],
            doc=f"doc:{concept['id']}",
        )
        traces += 1
        for link in concept.get("links") or []:
            other = bundle.get(link)
            if not other:
                continue
            kind = label_of(other)
            if kind == "Account":
                rel, a, b = "ABOUT_ACCOUNT", concept["id"], link
            elif kind == "Guideline":
                rel, a, b = "APPLIED_POLICY", concept["id"], link
            elif kind in {"Location", "Accumulation"}:
                rel, a, b = "CITED", concept["id"], link
            else:
                continue
            session.run(
                f"""
                MATCH (t:DecisionTrace {{id: $a}}), (n {{id: $b}})
                MERGE (t)-[:{rel}]->(n)
                """,
                a=a,
                b=b,
            )
            if kind == "Account":
                session.run(
                    """
                    MATCH (t:DecisionTrace {id: $t}), (acct:Account {id: $acct})
                    MERGE (acct)-[:CONTAINS]->(t)
                    MERGE (acct)-[:HAS_DECISION]->(t)
                    """,
                    t=concept["id"],
                    acct=link,
                )

    session.run(
        """
        MATCH (acct:Account)-[:HAS_LOCATION]->(loc:Location)
        MERGE (acct)-[:CONTAINS]->(loc)
        """
    )

    return {
        "documents": docs,
        "chunks": chunks,
        "communities": len(COMMUNITIES),
        "decision_traces": traces,
    }
