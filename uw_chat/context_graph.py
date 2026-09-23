"""Session context graph: the neighborhood already retrieved for this chat.

The knowledge graph in graph.json is the retrieval index. This module stores
the subgraph touched in the current Agent Engine session so a later question
can be answered without walking the ontology or calling Vertex AI Search again.

Before that neighborhood is shown to the model it is compacted to short facts,
deduplicated, pruned to the question, and reused from a session cache when the
same neighborhood is asked again.
"""

from __future__ import annotations

import re

from google.adk.tools.tool_context import ToolContext

from .graph_tool import seeds_for

_STATE_KEY = "context_graph"
_MAX_NODES = 20
_MAX_FACTS = 8
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_RULE = re.compile(r"[\|\s:-]+")
_DASHES = re.compile(r"-+")
_SPACE = re.compile(r"\s+")
_FACT = re.compile(
    r"\$[\d,.]+|\d+%|IKE-UW-\d+|referral|decline|required|cap|zone|tiv|flood|"
    r"bind|appetite|headroom|breach|elevation",
    re.IGNORECASE,
)


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


def _compact_text(text: str) -> list[str]:
    """Turn a markdown excerpt or search chunk into short fact lines."""
    cleaned = _LINK.sub(r"\1", text or "").replace("**", "").replace("`", "")
    facts: list[str] = []
    for raw in cleaned.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or _RULE.fullmatch(stripped):
            continue
        if "|" in stripped:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            cells = [cell for cell in cells if cell and not _DASHES.fullmatch(cell)]
            if len(cells) >= 2 and cells[0].lower() == "field":
                continue
            if len(cells) >= 2:
                facts.append(f"{cells[0]}: {cells[1]}")
            continue
        line = _SPACE.sub(" ", stripped.lstrip("-* ").strip())
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            sentence = sentence.strip()
            if sentence and _FACT.search(sentence):
                facts.append(sentence)
    return facts


def _norm_fact(fact: str) -> str:
    return re.sub(r"[^a-z0-9$%]+", " ", fact.lower()).strip()


_ANCHOR = re.compile(r"\$[\d,.]+m?|\d+%|ike-uw-\d+", re.IGNORECASE)
_SIGNAL_WORDS = ("referral", "decline", "required", "missing", "breach")


def _signals(fact: str) -> set[str]:
    found = {match.group(0).lower() for match in _ANCHOR.finditer(fact)}
    lowered = fact.lower()
    found.update(word for word in _SIGNAL_WORDS if word in lowered)
    return found


def _dedupe_facts(facts: list[str]) -> list[str]:
    """Drop a fact whose figures and decisions are already stated on this node."""
    kept: list[str] = []
    norms: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        fact = _SPACE.sub(" ", fact).strip(" -")
        norm = _norm_fact(fact)
        if not norm:
            continue
        if any(norm == previous or norm in previous for previous in norms):
            continue
        signals = _signals(fact)
        if signals and signals <= seen:
            continue
        kept.append(fact)
        norms.append(norm)
        seen.update(signals)
        if len(kept) >= _MAX_FACTS:
            break
    return kept


def _node_facts(node: dict) -> list[str]:
    facts = list(node.get("facts") or [])
    if not facts:
        facts = _compact_text(node.get("excerpt") or "")
        for passage in node.get("passages") or []:
            facts.extend(_compact_text(str(passage)))
    return _dedupe_facts(facts)


def _bump(stored: dict) -> int:
    stored["revision"] = int(stored.get("revision") or 0) + 1
    stored.pop("cache", None)
    return stored["revision"]


def _trim(stored: dict) -> None:
    """Drop the nodes least related to the latest retrieval once the cap is hit."""
    focus = {node_id for node_id in stored.get("focus_ids") or [] if node_id in stored["nodes"]}
    while len(stored["nodes"]) > _MAX_NODES:
        candidates = [node_id for node_id in stored["nodes"] if node_id not in focus] or list(stored["nodes"])
        drop = min(candidates, key=lambda node_id: int(stored["nodes"][node_id].get("focus") or 0))
        del stored["nodes"][drop]
        focus.discard(drop)
    kept = set(stored["nodes"])
    stored["edges"] = [
        edge
        for edge in stored["edges"]
        if edge["source"] in kept and edge["target"] in kept
    ]
    stored["focus_ids"] = [node_id for node_id in stored.get("focus_ids") or [] if node_id in kept]


def remember_expansion(tool_context: ToolContext, expansion: dict) -> None:
    """Merge an ontology expansion into the session context graph."""
    stored = _load(tool_context)
    if stored is None:
        return
    turn = _bump(stored)
    focus_ids: list[str] = []
    for node in expansion.get("nodes") or []:
        current = stored["nodes"].get(node["id"], {})
        facts = _dedupe_facts(
            _compact_text(node.get("excerpt") or "")
            + list(current.get("facts") or [])
            + [fact for passage in current.get("passages") or [] for fact in _compact_text(str(passage))]
        )
        stored["nodes"][node["id"]] = {
            "id": node["id"],
            "label": node["label"],
            "title": node["title"],
            "datastore": node.get("datastore") or "",
            "article_id": node.get("article_id") or "",
            "facts": facts,
            "focus": turn,
        }
        focus_ids.append(node["id"])
    stored["focus_ids"] = focus_ids
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
    changed = False
    for node in stored["nodes"].values():
        if node.get("datastore") != datastore:
            continue
        title = (node.get("title") or "").lower()
        if not title:
            continue
        extra: list[str] = []
        for result in results:
            content = (result.get("content") or "").strip()
            if not content or title not in content.lower():
                continue
            extra.extend(_compact_text(content))
        if not extra:
            continue
        merged = _dedupe_facts(list(node.get("facts") or []) + extra)
        if merged != list(node.get("facts") or []):
            node["facts"] = merged
            changed = True
    if changed:
        _bump(stored)
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
    if not covered:
        return {"covered": False, "reason": reason, "nodes": [], "edges": [], "cached": False}
    nodes, edges, cached = _prepare(stored, seed_ids)
    return {"covered": True, "reason": reason, "nodes": nodes, "edges": edges, "cached": cached}


def _active_ids(stored: dict) -> set[str]:
    """Nodes from the latest retrieval. Older accounts stay stored but stay out of 'that'."""
    nodes = stored["nodes"]
    focus = [node_id for node_id in stored.get("focus_ids") or [] if node_id in nodes]
    if focus:
        return set(focus)
    latest = max((int(node.get("focus") or 0) for node in nodes.values()), default=0)
    if latest <= 0:
        return set(nodes)
    return {node_id for node_id, node in nodes.items() if int(node.get("focus") or 0) == latest}


def _neighborhood(stored: dict, seed_ids: set[str]) -> tuple[list[dict], list[dict]]:
    view = set(seed_ids) if seed_ids else _active_ids(stored)
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


def _prepare(stored: dict, seed_ids: set[str]) -> tuple[list[dict], list[dict], bool]:
    """Compact the selected neighborhood, or reuse the cached view for this revision."""
    nodes, edges = _neighborhood(stored, seed_ids)
    key = [int(stored.get("revision") or 0), sorted(node["id"] for node in nodes)]
    cache = stored.get("cache")
    if isinstance(cache, dict) and cache.get("key") == key:
        return list(cache["nodes"]), list(cache["edges"]), True
    prepared = [
        {
            "id": node["id"],
            "label": node.get("label") or "",
            "title": node.get("title") or "",
            "article_id": node.get("article_id") or "",
            "facts": _node_facts(node),
        }
        for node in nodes
    ]
    slim = [
        {"source": edge["source"], "relation": edge["relation"], "target": edge["target"]}
        for edge in edges
    ]
    stored["cache"] = {"key": key, "nodes": prepared, "edges": slim}
    return prepared, slim, False


def consult_context_graph(query: str, tool_context: ToolContext) -> dict:
    """Read the session context graph for this chat.

    The knowledge graph is for retrieval. This graph is the working set from
    earlier turns. When covered is true, answer from nodes and edges and do
    not call the knowledge search agent.

    Args:
        query: The user question.
    """
    stored = _load(tool_context) or _blank()
    result = coverage_for(query, stored)
    if result["covered"]:
        tool_context.state[_STATE_KEY] = stored
    return result


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
    by_id = {node["id"]: node for node in result["nodes"]}
    lines = [
        "The session context graph already covers this question.",
        "These facts are compacted, deduplicated, and pruned to this question.",
        "Use only the facts listed. Do not blend figures from accounts that are not listed.",
        "Retrieval tools are not available on this turn. Do not search the book or the web.",
        "Begin with exactly:",
        "Source: context",
        "",
        "Nodes:",
    ]
    for node in result["nodes"]:
        article_id = node.get("article_id") or ""
        article = f" {article_id}" if article_id and article_id not in (node.get("title") or "") else ""
        lines.append(f"- {node.get('label')} {node.get('title')}{article}")
        for fact in node.get("facts") or []:
            lines.append(f"  - {fact}")
    lines.append("Edges:")
    for edge in result["edges"]:
        source = by_id.get(edge["source"], {}).get("title", edge["source"])
        target = by_id.get(edge["target"], {}).get("title", edge["target"])
        lines.append(f"- {source} {edge['relation']} {target}")
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
    callback_context.state[_STATE_KEY] = stored
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
