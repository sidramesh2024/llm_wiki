"""Root underwriting agent, knowledge search, and web search fallback."""

from google.adk.agents import Agent
from google.adk.tools import google_search
from google.adk.tools.agent_tool import AgentTool

from .graph_tool import expand_graph
from .search_tool import search_knowledge

MODEL = "gemini-2.5-flash"

knowledge_search_agent = Agent(
    name="knowledge_search_agent",
    model=MODEL,
    description=(
        "Searches the fictional commercial-property book: accounts, IKE guidelines, "
        "and exposure datastores, joined by the underwriting ontology."
    ),
    instruction="""
You answer only from the fictional underwriting book.

Datastores:
- accounts: Account (named insured)
- guidelines: Guideline (IKE article)
- exposures: Location and Accumulation

Relations you may cite: HAS_LOCATION, GOVERNED_BY, COUNTS_TOWARD, CAPPED_BY.

For every question:
1. Call expand_graph with the user question.
2. Call search_knowledge once per datastore that owns a returned node.
   Use the node title as the query. Also search the datastore that matches
   the question even if expand_graph missed it.
3. Answer only from expand_graph excerpts and search_knowledge passages.
   Prefer search passages when they contain the fact. Use excerpts to follow
   a relation the search chunk did not repeat.
4. Cite the path, for example Texwin -HAS_LOCATION-> Houston -GOVERNED_BY-> IKE-UW-310.
5. If those passages do not contain the fact, the first line must be exactly
   NOT_IN_CORPUS and the second line a short reason. Do not use outside knowledge
   and do not guess TIV, caps, or appetite.
6. If one search_knowledge call returns an error, search the other datastores
   and still use expand_graph excerpts. Use SEARCH_UNAVAILABLE as the first
   line only when every search failed and the excerpts do not contain the fact.
""",
    tools=[expand_graph, search_knowledge],
)

web_search_agent = Agent(
    name="web_search_agent",
    model=MODEL,
    description="Searches the public web when the underwriting book does not contain the answer.",
    instruction="""
The underwriting book did not contain this answer. Use google_search and answer
from those results. Cite the page title and URL for each material claim.
If the question asks for TIV, appetite, caps, or bind recommendations for
Axium Foods, Baxter International, Goya Foods, or Texwin Acquisitions, say those
figures are fictional book data and are not on the public web. Do not invent them.
If search does not support an answer, say so.
""",
    tools=[google_search],
)

root_agent = Agent(
    name="underwriting_root",
    model=MODEL,
    description="Underwriting desk. Uses the book first, then web search when the book has no answer.",
    instruction="""
You lead a fictional commercial-property desk. The book contains Axium Foods,
Baxter International, Goya Foods, and Texwin Acquisitions only.

1. Always call knowledge_search_agent first with the user question.
2. If its reply starts with NOT_IN_CORPUS, call web_search_agent and answer from that.
3. If its reply starts with SEARCH_UNAVAILABLE, do not call the web agent.
   Tell the user knowledge search failed and include the reason.
4. If the book answered, do not call the web agent unless the user also asked
   for a current public fact the book cannot have. Then call web_search_agent
   only for that public part.
5. Begin your reply with exactly one of these lines:
   Source: knowledge
   Source: web
   Source: knowledge+web
6. Then answer in plain prose. Do not invent book figures. Remind the user the
   book is fictional when you state TIV, caps, or a bind recommendation.
""",
    tools=[
        AgentTool(agent=knowledge_search_agent),
        AgentTool(agent=web_search_agent),
    ],
)
