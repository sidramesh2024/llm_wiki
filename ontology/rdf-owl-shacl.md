# Constructing the ontology from submissions, IKE, and exposure

How to go from **account submission**, **IKE**, and **exposure** documents to RDF/OWL + SHACL, then to the Neo4j knowledge layer the ADK agent walks.

Hand-define the TBox once ([`uw.ttl`](uw.ttl)). Extract an ABox per document. Validate with [`uw.shacl.ttl`](uw.shacl.ttl). Load triples into Neo4j as labels and relationship types the knowledge agent already uses.

**All figures in examples are fictional.**

---

## What you get from each source

Do not start by asking an LLM for a new schema. Read the three corpora and fill **these** classes. Propose extra types only if a real question cannot be answered with a property plus one object property (see [README.md](README.md)).

| Source | OWL class | Typical IRIs | Facts you extract | Edges you mint |
|---|---|---|---|---|
| Account submission (application, named insured, schedule of locations, requested limit) | `uw:Account` | `…/companies/{slug}` | `name`, `requestedLimit`, `occupancy`, `formed` | `hasLocation` → each scheduled site; `governedBy` → account-level IKE (e.g. thin named insured); `fromDocument` |
| IKE article (appetite, referral trigger, cap) | `uw:Guideline` | `…/ike/{slug}` | `articleId` (`IKE-UW-310`), rule text on the `Document`/`Chunk` | Usually none outbound. Incoming `governedBy` / `cappedBy` come from exposure and accum docs |
| Exposure **location** (TIV, flood, county, coast) | `uw:Location` | `…/exposure/{slug}` | `tiv`, `floodZone`, `county`, `milesToCoast`, `elevationCert` | `governedBy` → IKE that fires at the site; `countsToward` → county/CAT accum |
| Exposure **accumulation** (in-force vs cap) | `uw:Accumulation` | `…/exposure/accum-*` | `inForceTiv`, `cap` | `cappedBy` → the IKE that states the cap |

Every extracted entity also gets a `uw:Document` (the source file) and `uw:Chunk`s (sections). That is knowledge-layer **level 0**. Entities are **level 1**. Optional UW snapshots become `uw:DecisionTrace` (**level 2**).

```
Account submission ──extract──► Account ──hasLocation──► Location
IKE article         ──extract──► Guideline
Exposure location   ──extract──► Location ──governedBy──► Guideline
                                 Location ──countsToward──► Accumulation
Exposure accum      ──extract──► Accumulation ──cappedBy──► Guideline
```

Texwin Houston: the **submission** yields the Account and `hasLocation`; the **Houston exposure** yields TIV $38M, zone AE, `governedBy` IKE-UW-310, `countsToward` Harris; the **IKE-UW-310** document yields the Guideline (`articleId` IKE-UW-310). SHACL then checks: AE + TIV > $10M ⇒ that `governedBy` must exist.

---

## Construction steps

### 1. Freeze the TBox (OWL)

[`uw.ttl`](uw.ttl) is the contract. Classes match Neo4j labels; `rdfs:label` on object properties is the **relationship type** (`HAS_LOCATION`, `GOVERNED_BY`, …). Do not let the chat agent add classes at query time.

### 2. Extract an ABox from each document

For each file, emit Turtle (or JSON-LD) **instances only**:

1. Mint a `Document` IRI from the path (`doc:exposure/texwin-houston-whse`).
2. Split on headings → `Chunk` + `hasChunk` / `nextChunk` / `mentions`.
3. Classify: submission → `Account`; IKE + `article_id` → `Guideline`; exposure with `accum` in the id → `Accumulation`; else `Location`.
4. Copy typed fields (`tiv`, `floodZone`, `requestedLimit`, …).
5. Mint object properties only when the **target IRI** is known (link to another doc in the bundle, or a resolved named entity). Prefer `governedBy` from the **exposure** “IKE that fires” list, not from a fuzzy embedding.

An LLM extractor is allowed **if** its output vocabulary is closed: JSON keys = OWL properties in `uw.ttl`. Reject triples whose predicate is not in the TBox.

### 3. Validate with SHACL

```bash
pyshacl -s ontology/uw.shacl.ttl \
        -e ontology/uw.ttl \
        -d ontology/texwin.example.ttl
```

Shapes encode desk rules the Graph RAG walk assumes:

- Account has ≥1 `hasLocation` and a submission `Document`.
- Location has `tiv` and `fromDocument`; AE above $10M must `governedBy` IKE-UW-310.
- Guideline has `articleId` matching `IKE-UW-[0-9]+`.
- Accumulation has `cap` and `cappedBy`.
- DecisionTrace has `aboutAccount` and ≥1 `appliedPolicy`.

Invalid graphs do not load. That is the point of SHACL vs “the LLM said these nodes are related.”

### 4. Load into Neo4j (knowledge layer)

Map RDF → the labels `expand_graph` already walks.

| RDF | Neo4j |
|---|---|
| `a uw:Account` | `(:Account)` |
| `uw:hasLocation` | `-[:HAS_LOCATION]->` |
| `uw:id` | `n.id` (stable key for ADK / Vertex doc ids) |
| `uw:datastore` | `n.datastore` (`accounts` / `guidelines` / `exposures`) |
| `uw:fromDocument` | `-[:FROM_DOCUMENT]->(:Document)` |
| `uw:contains` / `uw:inCommunity` | knowledge-layer tree |

**Option A — neosemantics (n10s)** if the plugin is on the server:

```cypher
CALL n10s.graphconfig.init({
  handleVocabUris: 'SHORTEN',
  keepLangTag: false
});
CALL n10s.rdf.import.fetch("file:///ontology/texwin.example.ttl", "Turtle");
```

Then rename prefixes to the uppercase types the agent expects, or set `handleVocabUris` / mappings so `uw:hasLocation` becomes `HAS_LOCATION`.

**Option B — same Python loader this repo uses.** Keep OWL as the spec; `graph_rag/load.py` + `hierarchy.py` materialize nodes. Align property names with `uw.ttl` (`id`, `datastore`, `article_id`).

After load, the ADK knowledge agent does not read Turtle. It runs Cypher on those labels (`fetch_account`), stores the neighborhood on `context_graph`, and tool-calls Discovery Engine with `ref_doc_id` = `uw:id`.

---

## What the ADK agent relies on

```
OWL/SHACL  →  valid RDF ABox  →  Neo4j labels/rels
                                  expand_graph (Graph RAG)
                                  session context_graph
                                  search_knowledge(guidelines|exposures, ids)
```

If SHACL is skipped, Houston AE can exist without `governedBy` IKE-UW-310, and the agent’s typed walk will miss the flood referral. The ontology is not documentation; it is the **schema of the neighborhood** loaded onto the knowledge search agent.

Example ABox: [`texwin.example.ttl`](texwin.example.ttl). Human-readable types: [`underwriting.md`](underwriting.md).
