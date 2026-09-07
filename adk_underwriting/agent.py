"""ADK router: OKF graph first, then Company / IKE / Exposure specialists."""

from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool

from .tools import (
    get_company_profile,
    get_exposure_docs,
    get_ike_articles,
    okf_read_snapshot,
    okf_resolve_company,
)

MODEL = "gemini-2.5-flash"

company_agent = Agent(
    name="company_agent",
    model=MODEL,
    description="Fetches named-insured application facts from the Companies datastore.",
    instruction=(
        "You are the Companies datastore specialist. "
        "Call get_company_profile with the company_id the router gives you. "
        "Return occupancy, requested limit, locations, and named-insured facts only. "
        "Do not interpret IKE appetite or CAT numbers."
    ),
    tools=[get_company_profile],
)

ike_agent = Agent(
    name="ike_agent",
    model=MODEL,
    description="Fetches underwriting IKE guideline articles that the OKF linked to this company.",
    instruction=(
        "You are the IKE guidelines specialist. "
        "Call get_ike_articles with the comma-separated ike_ids from the router. "
        "Quote appetite caps, referral triggers, and deductibles. "
        "Do not invent articles that were not requested."
    ),
    tools=[get_ike_articles],
)

exposure_agent = Agent(
    name="exposure_agent",
    model=MODEL,
    description="Fetches TIV, flood, CAT, and accumulation docs from the Exposure datastore.",
    instruction=(
        "You are the Risk Exposure specialist. "
        "Call get_exposure_docs with the comma-separated exposure_ids from the router. "
        "Return TIV, flood zone, distance to coast, and accumulation arithmetic. "
        "Do not restate full IKE policy language."
    ),
    tools=[get_exposure_docs],
)

root_agent = Agent(
    name="underwriting_router",
    model=MODEL,
    description=(
        "Router for property underwriting questions. Resolves the company in the "
        "OKF graph, then calls company, IKE, and exposure specialists."
    ),
    instruction="""
You are a simple underwriting router. You do not search the three datastores
yourself. You use the OKF knowledge graph to decide *which* documents the
specialists should fetch, then you invoke all three sub-agents and synthesize.

Always follow this order:

1. Call okf_resolve_company with the applicant named in the user question
   (Axium Foods Inc, Baxter International, Goya Foods Inc, or Texwin Acquisitions LLC).
2. If a snapshot_id is returned, call okf_read_snapshot for the compiled join page.
3. Invoke company_agent with the company_id.
4. Invoke ike_agent with every returned ike id (comma-separated).
5. Invoke exposure_agent with every returned exposure id (comma-separated).
6. Answer the user in this shape:
   - Recommendation (bind / referral / decline) using only specialist facts
   - IKE articles that fired, with caps and triggers
   - Exposure numbers (TIV, flood, CAT, accumulation)
   - Shared-graph notes (e.g. Goya and Baxter both hit Puerto Rico CAT)

Never skip a specialist because you think the snapshot is enough.
Never pull IKE or exposure for a company the OKF did not link.
All bundle figures are mock underwriting data.
""",
    tools=[
        okf_resolve_company,
        okf_read_snapshot,
        AgentTool(agent=company_agent),
        AgentTool(agent=ike_agent),
        AgentTool(agent=exposure_agent),
    ],
)
