# llm_wiki

Mock LLM-wiki / [Open Knowledge Format](https://github.com/GoogleCloudPlatform/open-knowledge-format) bundle plus a Google ADK router that walks the graph, then calls Company, IKE, and Exposure specialists.

**All underwriting figures are fictional.** Named companies are used only as example applicants.

## Layout

```
okf/                  # OKF bundle (markdown concepts + [[links]])
  companies/          # Axium Foods, Baxter International, Goya Foods, Texwin Acquisitions
  ike/                # Mock IKE guideline articles
  exposure/           # Mock TIV / CAT / flood / accumulation docs
  syntheses/          # Compiled UW snapshots
adk_underwriting/     # ADK router + three sub-agents
```

## Run the agent

```bash
pip install -r requirements.txt
adk web adk_underwriting
```

Example questions:

- Can we write Texwin Acquisitions LLC for the Houston warehouse?
- If we bind Baxter and Goya, do we breach the Puerto Rico cap?
- What IKE articles fire for Axium Foods Inc?

The router calls `okf_resolve_company` first, then invokes `company_agent`, `ike_agent`, and `exposure_agent` with only the linked document ids.

On GCP, replace the mock datastore tools in `adk_underwriting/tools.py` with `VertexAiSearchTool` pointed at your three Vertex AI Search datastores. Keep the OKF resolve step as the graph layer.
