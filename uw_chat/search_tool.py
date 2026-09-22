"""Search one of the three underwriting Vertex AI Search datastores."""

from __future__ import annotations

from typing import Literal

from .gcp_config import DATA_STORES, PROJECT_ID, serving_config

StoreName = Literal["accounts", "guidelines", "exposures"]
_client = None


def _search_client():
    global _client
    if _client is None:
        from google.api_core.client_options import ClientOptions
        from google.cloud import discoveryengine_v1beta as discoveryengine

        _client = discoveryengine.SearchServiceClient(
            client_options=ClientOptions(quota_project_id=PROJECT_ID)
        )
    return _client


def search_knowledge(datastore: StoreName, query: str) -> dict:
    """Search one Vertex AI Search datastore in the underwriting book.

    Args:
        datastore: accounts for named insureds, guidelines for IKE articles,
            exposures for locations and accumulations.
        query: Search text, usually an entity title from expand_graph plus the question.
    """
    store_id = DATA_STORES.get(datastore)
    if not store_id:
        return {
            "status": "error",
            "error_message": "datastore must be accounts, guidelines, or exposures",
        }
    try:
        from google.api_core.exceptions import GoogleAPICallError
        from google.cloud import discoveryengine_v1beta as discoveryengine
    except Exception as exc:  # pragma: no cover - import guard for the runtime image
        return {"status": "error", "error_message": str(exc)}

    spec_cls = discoveryengine.SearchRequest.ContentSearchSpec
    request = discoveryengine.SearchRequest(
        serving_config=serving_config(store_id),
        query=query,
        page_size=5,
        content_search_spec=spec_cls(
            search_result_mode=spec_cls.SearchResultMode.CHUNKS,
            chunk_spec=spec_cls.ChunkSpec(num_previous_chunks=0, num_next_chunks=0),
        ),
    )
    try:
        response = _search_client().search(request)
    except GoogleAPICallError as exc:
        return {"status": "error", "datastore": datastore, "error_message": str(exc)}

    results = []
    for item in response.results:
        chunk = item.chunk
        if not chunk or not chunk.content:
            continue
        title = ""
        if chunk.document_metadata and chunk.document_metadata.title:
            title = chunk.document_metadata.title
        results.append({"title": title, "content": chunk.content[:1500]})
    return {"status": "success", "datastore": datastore, "results": results}
