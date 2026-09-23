"""Session context graph: the neighborhood already retrieved for this chat.

The knowledge graph in graph.json is the retrieval index. This module stores
the subgraph touched in the current Agent Engine session so a later question
can be answered without walking the ontology or calling Vertex AI Search again.
"""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext

from .graph_tool import seeds_for

_STATE_KEY = "context_graph"
_MAX_NODES = 20


def _blank() -> dict:
    return {"nodes": {}, "edges": []}


def _load(tool_context: ToolContext | None) -> dict | None:
    if tool_context is None:
        return None
    raw = tool_context.state.get(_STATE_KEY)
    if not isinstance(raw, dict):
        raw = _blank()
    raw.setdefault("nodes", {})
    raw.setdefault("edges", [])
    return raw


def _trim(stored: dict) -> None:
    while len(stored["nodes"]) > _MAX_NODES:
        oldest = next(iter(stored["nodes"]))
        del stored["nodes"][oldest]
    kept = set(stored["nodes"])
    stored["edges"] = [
        edge
        for edge in stored["edges"]
        if edge["source"] in kept and edge["target"] in kept
    ]


def remember_expansion(tool_context: ToolContext, expansion: dict) -> None:
    """Merge an ontology expansion into the session context graph."""
    stored = _load(tool_context)
    if stored is None:
        return
    for node in expansion.get("nodes") or []:
        current = stored["nodes"].get(node["id"], {})
        stored["nodes"][node["id"]] = {**node, "passages": list(current.get("passages") or [])}
    seen = {(edge["source"], edge["relation"], edge["target"]) for edge in stored["edges"]}
    for edge in expansion.get("edges") or []:
        key = (edge["source"], edge["relation"], edge["target"])
        if key in seen:
            continue
        stored["edges"].append(
            {"source": edge["source"], "relation": edge["relation"], "target": edge["target"]}
        )
        seen.add(key)
    _trim(stored)
    tool_context.state[_STATE_KEY] = stored


def remember_passages(tool_context: ToolContext, datastore: str, results: list[dict]) -> None:
    """Attach search passages to context-graph nodes they quote."""
    stored = _load(tool_context)
    if stored is None:
        return
    for node in stored["nodes"].values():
        if node.get("datastore") != datastore:
            continue
        title = (node.get("title") or "").lower()
        if not title:
            continue
        for result in results:
            content = (result.get("content") or "").strip()
            if not content or title not in content.lower():
                continue
            passages = list(node.get("passages") or [])
            snippet = content[:800]
            if snippet not in passages:
                node["passages"] = (passages + [snippet])[:2]
    tool_context.state[_STATE_KEY] = stored


def coverage_for(query: str, stored: dict) -> dict:
    """Decide whether this question can be answered from the stored neighborhood."""
    stored.setdefault("nodes", {})
    stored.setdefault("edges", [])
    seeds = seeds_for(query)
    seed_ids = {node["id"] for node in seeds}
    have = set(stored["nodes"])
    if seed_ids and seed_ids <= have:
        covered = True
        reason = "Named entities are already in the session context graph."
    elif not seed_ids and stored["nodes"]:
        covered = True
        reason = "The question names no new entity, so the session context graph still applies."
    else:
        covered = False
        missing = [node["title"] for node in seeds if node["id"] not in have]
        reason = (
            "Not in the context graph: " + ", ".join(missing)
            if missing
            else "The context graph is empty."
        )
    nodes, edges = _neighborhood(stored, seed_ids) if covered else ([], [])
    return {"covered": covered, "reason": reason, "nodes": nodes, "edges": edges}


def _neighborhood(stored: dict, seed_ids: set[str]) -> tuple[list[dict], list[dict]]:
    view = set(seed_ids) if seed_ids else set(stored["nodes"])
    if seed_ids:
        changed = True
        while changed:
            changed = False
            for edge in stored["edges"]:
                if edge["source"] in view and edge["target"] in stored["nodes"] and edge["target"] not in view:
                    view.add(edge["target"])
                    changed = True
                if edge["target"] in view and edge["source"] in stored["nodes"] and edge["source"] not in view:
                    view.add(edge["source"])
                    changed = True
    nodes = [stored["nodes"][node_id] for node_id in view if node_id in stored["nodes"]]
    edges = [
        edge
        for edge in stored["edges"]
        if edge["source"] in view and edge["target"] in view
    ]
    return nodes[:_MAX_NODES], edges


def consult_context_graph(query: str, tool_context: ToolContext) -> dict:
    """Read the session context graph for this chat.

    The knowledge graph is for retrieval. This graph is the working set from
    earlier turns. When covered is true, answer from nodes and edges and do
    not call the knowledge search agent.

    Args:
        query: The user question.
    """
    stored = _load(tool_context) or _blank()
    return coverage_for(query, stored)


_RETRIEVAL_TOOLS = {
    "consult_context_graph",
    "knowledge_search_agent",
    "web_search_agent",
}


def _latest_unanswered_question(contents) -> str | None:
    """User text for this turn, or None once a tool has already run."""
    for content in reversed(contents or []):
        parts = getattr(content, "parts", None) or []
        if any(
            getattr(part, "function_call", None) or getattr(part, "function_response", None)
            for part in parts
        ):
            return None
        texts = [part.text for part in parts if getattr(part, "text", None)]
        if texts and (getattr(content, "role", None) or "user") == "user":
            return "\n".join(texts)
    return None


def _drop_retrieval_tools(llm_request) -> None:
    for name in list(llm_request.tools_dict):
        if name in _RETRIEVAL_TOOLS:
            del llm_request.tools_dict[name]
    tools = list(llm_request.config.tools or [])
    kept = []
    for tool in tools:
        declarations = getattr(tool, "function_declarations", None)
        if not declarations:
            kept.append(tool)
            continue
        remaining = [item for item in declarations if item.name not in _RETRIEVAL_TOOLS]
        if remaining:
            tool.function_declarations = remaining
            kept.append(tool)
    llm_request.config.tools = kept or None


def _context_prompt(result: dict) -> str:
    lines = [
        "The session context graph already covers this question.",
        "Retrieval tools are not available on this turn. Do not search the book or the web.",
        "Answer only from the nodes and edges below. Begin with exactly:",
        "Source: context",
        "",
        "Nodes:",
    ]
    for node in result["nodes"]:
        lines.append(
            f"- {node.get('label')} {node.get('title')} "
            f"[{node.get('datastore')}] {node.get('article_id') or ''}".rstrip()
        )
        excerpt = (node.get("excerpt") or "").strip()
        if excerpt:
            lines.append("  excerpt: " + excerpt[:500])
        for passage in (node.get("passages") or [])[:2]:
            lines.append("  passage: " + str(passage)[:500])
    lines.append("Edges:")
    for edge in result["edges"]:
        lines.append(f"- {edge['source']} {edge['relation']} {edge['target']}")
    return "\n".join(lines)


def use_context_graph_when_covered(callback_context, llm_request):
    """Answer a covered follow-up from the session graph in one model call.

    The knowledge graph stays the retrieval index. Once a neighborhood is
    stored, a later question that does not name a new entity skips
    expand_graph and Vertex AI Search.
    """
    question = _latest_unanswered_question(llm_request.contents)
    if not question:
        return None
    stored = callback_context.state.get(_STATE_KEY)
    if not isinstance(stored, dict) or not stored.get("nodes"):
        return None
    result = coverage_for(question, stored)
    if not result["covered"] or not result["nodes"]:
        return None
    llm_request.append_instructions([_context_prompt(result)])
    _drop_retrieval_tools(llm_request)
    callback_context.state["temp:context_fast_path"] = True
    return None


def label_context_answer(callback_context, llm_response):
    """Force the source line when this turn was answered from the context graph."""
    if not callback_context.state.get("temp:context_fast_path"):
        return None
    if getattr(llm_response, "partial", None):
        return None
    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None) or []
    if any(getattr(part, "function_call", None) for part in parts):
        return None
    text = "\n".join(part.text for part in parts if getattr(part, "text", None)).strip()
    if not text:
        return None
    body = text
    if body.lower().startswith("source:"):
        body = "\n".join(body.splitlines()[1:]).strip()
    if text.lower().startswith("source: context"):
        return None
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text="Source: context\n" + body)],
        )
    )
