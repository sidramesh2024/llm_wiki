# What an ontology is, and how it is used here

The typed graph for this desk is not “whatever the LLM extracted this morning.” It is an **ontology**: a small, agreed list of entity types and allowed edges. Cypher, session context, and Discovery Engine tool calls all assume that list.

The schema itself is [underwriting.md](underwriting.md). This note is *why it exists*, **why Graph RAG beats vector or wiki retrieval on this desk**, and whether you should write types by hand or let an agent invent them.

**All underwriting figures are fictional.**

---

## What an ontology is

An ontology is a **shared vocabulary for a domain**, with two parts:

1. **Types** — what kinds of things exist. Here: Account, Location, Guideline, Accumulation.
2. **Typed edges** — which relations are allowed between which types. Here: `HAS_LOCATION`, `GOVERNED_BY`, `COUNTS_TOWARD`, `CAPPED_BY`.

It is not the data. Texwin and Houston are **instances**. `Account` and `HAS_LOCATION` are **ontology**. The knowledge pipeline *fills* the graph; the ontology *constrains* what can be written and what Cypher may walk.

A wiki page with `[[links]]` has no types on the edges. A vector index has no types at all. Graph RAG is useful only if a Houston node is a Location, not a blob, and the edge to IKE-UW-310 is `GOVERNED_BY`, not “related.”

```
ontology (contract)     Account, Location, Guideline, Accumulation
                        HAS_LOCATION, GOVERNED_BY, COUNTS_TOWARD, CAPPED_BY
        │
        ▼
pipeline (instances)    Texwin, Houston warehouse, IKE-UW-310, Harris cap
        │
        ▼
Neo4j                   nodes + typed edges + ref_doc_id
        │
        ▼
query                   match names → walk only allowed edges → fetch those docs
```

---

## How it is useful on this desk

The underwriter questions are **joins**, not keyword search:

- Can we write Texwin for Houston?
- Does that breach the Harris County cap?
- Which IKE articles fire at this site?

Without an ontology, “warehouse” links Texwin to Goya because both occupy warehouses. With it, the knowledge agent walks **only**:

```
(:Account)-[:HAS_LOCATION]->(:Location)-[:GOVERNED_BY]->(:Guideline)
(:Location)-[:COUNTS_TOWARD]->(:Accumulation)-[:CAPPED_BY]->(:Guideline)
```

That is what [uw_chat/README.md](../uw_chat/README.md) means by neighborhood + ref doc ids.

| Job | Why the ontology matters |
|---|---|
| Graph traversal | Cypher is schema-aware. Blind `[*1..2]` through a shared guideline pulls another book. |
| Discovery Engine | Each node type maps to a datastore (`guidelines` vs `exposures`). The agent tools the **ids the graph selected**, not the raw question. |
| Session context | Turn 2 asks “does that breach the cap?” The stored nodes are still Locations and Accumulations, so “that” is Harris, not a similar paragraph in Vertex. |
| Extraction | The pipeline must emit these types and edges. Extra free-text relations are not queryable unless you add them to the ontology first. |
| Accuracy vs wiki | Wiki compile-at-ingest uses undifferentiated links. The ontology is why Graph RAG can start at Houston and still reach IKE-310 without dumping Dallas unless you load the whole account on purpose. |

Attributes such as TIV, flood zone, or formed-date are **properties on instances**, not new types, until you need to query them as nodes (for example many flood zones sharing one guideline).

---

## Advantage of Graph RAG

Vector search (Vertex / Discovery Engine alone) finds **similar text**. The wiki finds **pages that already link to each other**. Graph RAG finds **the facts that are connected for this question**, then (optionally) loads only those IKE and exposure docs.

That is the advantage for this chat app.

| | Vector RAG only | Wiki hop-1 | Graph RAG |
|---|---|---|---|
| What you retrieve | Top-k chunks that look like the question | Everything linked from the Company page | A typed neighborhood (account, site, IKE, cap) |
| “Houston warehouse” | May miss the Harris cap if the chunk never says “Houston” and “$350M” together | Misses unless Houston is a Company title; or dumps Dallas and San Antonio too | Starts at Houston, follows `COUNTS_TOWARD` / `GOVERNED_BY` |
| Shared words (“warehouse”) | Goya and Texwin compete in the ranking | Both books sit on the same IKE page | `HAS_LOCATION` keeps books apart |
| Multi-hop | The model must stitch chunks | Join was pre-written on the page | Join is walked at query time |
| Citations | Whatever chunk ranked | The wiki page | Ref doc ids on the nodes → Discovery Engine for those ids only |
| Follow-up “does that breach the cap?” | New search, easy to mix Harris and Puerto Rico | Same compiled page, or miss | Session already holds the Harris accumulation node |

**Accuracy.** Underwriting questions *are* paths: named insured → location → guideline → accumulation cap. Graph RAG encodes those paths. The model is not asked to notice that $38M Houston plus $318M in-force is $356M vs a $350M cap because two unrelated chunks happened to appear in the prompt. The cap node is in the neighborhood **because of an edge**.

**Precision / less noise.** You do not send the whole book or the whole account dump unless you choose to. A Houston question can stay on Houston’s IKE and Harris, instead of every Texwin warehouse (wiki) or every “flood” paragraph in Vertex (vector).

**Grounded tool calls.** The graph returns **ref doc ids**. The knowledge agent then pulls IKE and exposure chunks for *those* documents. Discovery Engine becomes evidence, not the router. That is faster and cleaner than embedding the user sentence into three datastores.

**Multi-turn.** The neighborhood is a real graph you can keep on the session. Turn 2 can answer from context; if wording is missing, it retries Vertex with the same ids. Vector RAG has no such object—only a new kNN.

**Inspectable.** You can show `Texwin -HAS_LOCATION-> Houston -GOVERNED_BY-> IKE-UW-310`. A ranking score on a chunk does not explain a referral.

Graph RAG does **not** replace document text. Node properties and Vertex chunks still carry TIV, flood zone, and IKE language. The advantage is **which** text you load, and **why** it belongs in the answer.

---

## Manual vs LLM-discovered types and edges

**Define the core ontology by hand. Use an LLM to propose subentities and extra edges, then freeze what you accept.**

Do not let the knowledge search agent invent types at **query** time. That would change the graph every question and break Cypher, session context, and datastore routing.

### Define by hand (the contract)

Write the types and edges a human underwriter already uses. For this book that is the four types and four relations in [underwriting.md](underwriting.md).

Hand-defined ontology is the right default when:

- The questions are known (write / referral / cap / which IKE fires).
- Datastores are already split (accounts, guidelines, exposures).
- You need stable Cypher (`MATCH (loc)-[:GOVERNED_BY]->(g)`).
- Wrong edges are costly (shared occupancy pulling another insured).

A small, boring schema outperforms a large, clever one. Four types is enough to load Texwin’s neighborhood and fetch IKE + exposure chunks.

### Use an LLM agent (drafts only)

An extraction or “ontology miner” agent is useful **offline**, on a sample of source docs, when you do not yet know the vocabulary:

- Propose **subentities**: FloodZone, Occupancy, County, NamedStormBand, Construction, BI%.
- Propose **edges**: `IN_FLOOD_ZONE`, `HAS_OCCUPANCY`, `IN_COUNTY`.
- Flag phrases that look like rules (“referral above $10M”) that should be Guideline text, not a new type.

Treat that output as a **pull request against the ontology**, not as live graph writes. Accept a type only if:

1. You will query it (Cypher or a tool filter).
2. It has a clear from/to pair (not “related_to”).
3. You know which datastore / ref doc owns it.
4. It does not duplicate a property (flood zone AE can stay on Location until you need Zone as a hub).

Then the **instance** pipeline (LLM or rules) extracts *into* that frozen schema: “this paragraph is a Location, this link is `GOVERNED_BY` IKE-UW-310.” Extraction may be automatic. The allowed labels should not be.

### What not to do

| Approach | Problem |
|---|---|
| One LLM invents nodes and edges per document, no schema | Graph becomes a second wiki of random links. Traversal is noisy. |
| Knowledge agent adds types during chat | Session context and Cypher no longer match. |
| Huge industry ontology (full ACORD / every IKE cross-ref) on day one | Over-modelled; the chat still asks four questions. |
| No ontology, Vertex only | Fast keyword hits, weak multi-hop (Houston → cap → IKE-510). |

---

## Practical split for this repo

```
Human (once)     ontology/underwriting.md
                 Account, Location, Guideline, Accumulation
                 HAS_LOCATION, GOVERNED_BY, COUNTS_TOWARD, CAPPED_BY

LLM (optional)   read a sample of IKE + exposure docs
                 propose FloodZone, County, Occupancy, …
                 you merge a few into underwriting.md

Pipeline         extract instances into Neo4j using only accepted types
                 each node: ref_doc_id + datastore

Chat             expand_graph walks the frozen schema
                 miss on wording → search_knowledge(those ref_doc_ids)
```

Start manual. Add a subentity when a real question cannot be answered by a property plus one of the four edges. If you want an ontology-miner agent later, it should write a markdown diff against [underwriting.md](underwriting.md), not mutate Aura during a user turn.
