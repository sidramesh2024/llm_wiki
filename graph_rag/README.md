# LLM wiki vs Graph RAG

Same four mock accounts (Axium, Baxter, Goya, Texwin), two retrieval designs. Figures are fictional.

The wiki **compiles a neighborhood onto the Company page at ingest**. Graph RAG **matches entities in the question and expands typed Neo4j paths at query time**.

Run them side by side:

```bash
pip install -r requirements.txt
./scripts/start_neo4j.sh
python3 -m graph_rag.load
python3 -m graph_rag "Can we write Texwin Acquisitions for the Houston warehouse?"
python3 -m graph_rag "What IKE articles fire for the Houston warehouse?"
```

Neo4j Browser: [http://127.0.0.1:7474](http://127.0.0.1:7474) — user `neo4j`, password `llmwiki1` (local only). Stop with `$HOME/.local/neo4j/neo4j-community-5.26.30/bin/neo4j stop`.

---

## What each graph actually is

### LLM wiki (this repo's `okf/` + ADK router)

- **Nodes** = markdown pages (`Company`, `IKE Guideline`, `Exposure`, `UW Snapshot`).
- **Edges** = undifferentiated `[[wikilinks]]`. The link type is not stored.
- **Query path:** extract a company name → `okf_resolve_company` → hop-1 from that **Company** page → pass those ids to Company / IKE / Exposure specialists.
- The useful join is already written on the account page and in `okf/syntheses/`. Chat reads the compiled neighborhood; it does not re-walk typed relations for each question.

See [okf/companies/texwin-acquisitions.md](../okf/companies/texwin-acquisitions.md): Houston, Dallas, San Antonio, Harris accum, and four IKE articles are already linked.

### Graph RAG (this package + Neo4j)

- **Nodes** are typed: `Account`, `Location`, `Guideline`, `Accumulation`.
- **Edges** are typed:

```
(:Account)-[:HAS_LOCATION]->(:Location)-[:GOVERNED_BY]->(:Guideline)
(:Location)-[:COUNTS_TOWARD]->(:Accumulation)-[:CAPPED_BY]->(:Guideline)
(:Account)-[:GOVERNED_BY]->(:Guideline)
(:Account)-[:COUNTS_TOWARD]->(:Accumulation)
```

- **Query path:** cheap entity link on the question (`houston`, `texwin`, …) → start at the best-matching label (a **Location** if a city is present, otherwise an **Account**) → schema-aware Cypher → node text into an LLM.

`load.py` derives those types from OKF folders and frontmatter, then stores canonical directions in Neo4j. Snapshots are skipped on purpose: Graph RAG is meant to **rebuild the join at query time**, not read the pre-filed UW snapshot.

---

## The teaching question

> Can we write Texwin Acquisitions for the Houston warehouse?

| | LLM wiki | Graph RAG |
|---|---|---|
| Start node | Must be a **Company** page | Can be **Houston** (a `Location`) |
| If you pass the whole sentence to resolve | Miss — it is not a Company title | Links needles `texwin` + `houston`, starts at Houston |
| If the router extracts **Texwin** | Hop-1 from the account page | Location-first walk, then one hop to the account |
| Houston | Does not filter the neighborhood | Filters: Dallas and San Antonio are not in the subgraph |
| IKE-UW-310 flood AE | On the account page (all Texwin IKE) | On the Houston location (`GOVERNED_BY`) |
| IKE-UW-410 thin named insured | On the account page | On the **account**, reached by `HAS_LOCATION` then `GOVERNED_BY` |
| Harris $350M cap | Linked exposure + IKE on the account | `COUNTS_TOWARD` → accumulation → `CAPPED_BY` IKE-UW-510 |
| Extra sites | Dallas + San Antonio always come along | Not returned on the Houston start |

Second question — *What IKE articles fire for the Houston warehouse?* — is the sharp miss: wiki resolve never finds a Company named Houston. Graph RAG still returns Texwin, flood AE, warehouse occupancy, and the Harris cap.

---

## What the runner prints

`python3 -m graph_rag` calls both stacks on the same string.

**Wiki**, after extracting Texwin:

```
IKE: ike/flood-zone-ae, ike/harris-county-accumulation,
     ike/occupancy-warehouse, ike/thin-named-insured
Exposure: exposure/accum-harris-county, exposure/texwin-dallas-whse,
          exposure/texwin-houston-whse, exposure/texwin-san-antonio-whse
Snapshot: syntheses/texwin-acquisitions-uw
```

**Graph RAG**, starting at Houston:

```
Texwin Acquisitions LLC -[HAS_LOCATION]-> Texwin Houston warehouse
Texwin Houston warehouse -[GOVERNED_BY]-> IKE-UW-310 Flood zone AE
Texwin Houston warehouse -[GOVERNED_BY]-> IKE-UW-124 Warehouse occupancy
Texwin Houston warehouse -[GOVERNED_BY]-> IKE-UW-510 Harris County accumulation
Texwin Houston warehouse -[COUNTS_TOWARD]-> Harris County accumulation
Harris County accumulation -[CAPPED_BY]-> IKE-UW-510 Harris County accumulation
Texwin Acquisitions LLC -[GOVERNED_BY]-> IKE-UW-410 Thin named insured
```

Dallas and San Antonio stay off the Houston walk. IKE-410 still appears because the pattern takes one extra hop to the account.

In Neo4j Browser:

```cypher
MATCH (loc:Location {name: 'Texwin Houston warehouse'})-[r]-(n)
RETURN loc, r, n
```

---

## Why they feel different

**Compile at ingest (wiki).** An underwriter (or an LLM ingest job) already decided that Texwin *has* those locations and *is governed by* those IKE articles, and wrote it in markdown. Query time is routing: find the account page, read its links, optionally read the snapshot. Cheap, inspectable, good citations if the pages are the source of truth. Weak when the question is not about the account (`Houston`, `Harris cap`, `Puerto Rico`) or when you need an edge type the wiki never stored.

**Expand at query time (Graph RAG).** The store knows `HAS_LOCATION` vs `GOVERNED_BY` vs `CAPPED_BY`. The question can start at a location, a cap, or an account. You pay for entity linking and for not exploding through shared guidelines (blind 2-hop from Texwin via IKE-UW-124 also reaches Goya warehouses). The Cypher in `query.py` is schema-aware on purpose: it does not do unbounded `[*1..2]`.

They stack. Wiki / OKF can still route *which ids matter*; Vertex or Neo4j can still hold the typed walk and the evidence chunks.

---

## Layout

```
okf/                      # wiki pages (source for both)
adk_underwriting/tools.py # okf_resolve_company — wiki hop-1
graph_rag/load.py         # OKF → typed Neo4j
graph_rag/query.py        # side-by-side retrieve
scripts/start_neo4j.sh    # local Community 5.26 (user-space tarball)
```

Env (see [`.env.example`](../.env.example)): `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`.
