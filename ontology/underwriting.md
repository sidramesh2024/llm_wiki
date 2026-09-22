# Commercial property underwriting ontology

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

## How a question is answered

1. Link names in the question to Account, Location, Accumulation, or Guideline nodes.
2. Walk one or two typed hops. A Houston question starts at the Houston location, not at every Texwin site.
3. Search the datastore that owns each visited node (`accounts`, `guidelines`, or `exposures`).
4. If those passages do not contain the fact, the web search agent answers instead.
