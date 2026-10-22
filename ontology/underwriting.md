# Commercial property underwriting ontology

What an ontology is, why this desk needs one, and whether to write types by hand or let an LLM propose them: [README.md](README.md).

RDF/OWL + SHACL (TBox, shapes, Texwin ABox, Neo4j/ADK load): [rdf-owl-shacl.md](rdf-owl-shacl.md).

Use case: a chatbot for a fictional commercial-property desk. The underwriter asks whether an account can be written, which guidelines fire at a location, and whether an accumulation breaches its cap. Every figure in the book is fictional. Questions the book does not contain (news, weather, public company facts) go to web search.

## Entity types

| Type | Meaning | Datastore | Id pattern |
|---|---|---|---|
| Account | Named insured and application facts | `uw-accounts` | `companies/*` |
| Guideline | IKE underwriting article (appetite, cap, referral trigger) | `uw-guidelines` | `ike/*` |
| Location | Scheduled site: TIV, flood zone, coast distance | `uw-exposures` | `exposure/*` except accumulations |
| Accumulation | In-force aggregate and the cap it is tested against | `uw-exposures` | `exposure/accum*` |

Compiled UW snapshots (`syntheses/*`) are not in the ontology. Graph RAG rebuilds the join at question time from the relations below.

## Relations

| Relation | From | To | Meaning |
|---|---|---|---|
| `HAS_LOCATION` | Account | Location | Site scheduled on the account |
| `GOVERNED_BY` | Account or Location | Guideline | Guideline that applies to that node |
| `COUNTS_TOWARD` | Account or Location | Accumulation | Exposure that adds into an aggregate |
| `CAPPED_BY` | Accumulation | Guideline | Article that states the aggregate cap |

## Knowledge-layer hierarchy

The Neo4j graph is stacked like Neo4j’s [knowledge layer](https://neo4j.com/blog/agentic-ai/knowledge-layer/): source documents at the bottom, typed entities in the middle, decision traces and communities on top.

| Level | Node labels | Meaning |
|---|---|---|
| 0 | `Document`, `Chunk` | IKE / exposure / account source text (`HAS_CHUNK`, `NEXT_CHUNK`, `MENTIONS`) |
| 1 | `Account`, `Location`, `Guideline`, `Accumulation` | Canonical knowledge graph |
| 2 | `DecisionTrace`, `Community` | UW snapshots (policies applied, cited sites) and theme communities |
| 3 | `KnowledgeLayer` | Root. `CONTAINS` down the tree |

Communities use GraphRAG-style parent links: `IN_COMMUNITY`, `PARENT_COMMUNITY` (level 1 themes under the level 2 property book). Decision traces use `ABOUT_ACCOUNT`, `APPLIED_POLICY`, `CITED` so an agent can show *why* a referral was recommended, not only which nodes exist.

In Neo4j Browser:

```cypher
MATCH path = (:KnowledgeLayer {id: 'layer/uw-desk'})-[:CONTAINS*1..4]->(n)
RETURN path
```

## How a question is answered

1. Link names in the question to Account, Location, Accumulation, or Guideline nodes.
2. Walk one or two typed hops. A Houston question starts at the Houston location, not at every Texwin site.
3. Search the datastore that owns each visited node (`accounts`, `guidelines`, or `exposures`).
4. If those passages do not contain the fact, the web search agent answers instead.
