"""Walk the underwriting ontology shipped with the agent."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from google.adk.tools.tool_context import ToolContext

_GRAPH_PATH = Path(__file__).with_name("graph.json")
_WORD = re.compile(r"[a-z0-9]{4,}")
_GENERIC = {
    "about",
    "accumulation",
    "article",
    "articles",
    "bind",
    "book",
    "breach",
    "coastal",
    "cooking",
    "county",
    "does",
    "fire",
    "flood",
    "foods",
    "from",
    "have",
    "insured",
    "international",
    "limit",
    "named",
    "occupancy",
    "plant",
    "processing",
    "product",
    "storage",
    "storm",
    "that",
    "thin",
    "this",
    "warehouse",
    "what",
    "when",
    "where",
    "which",
    "with",
    "write",
    "zone",
}


@lru_cache(maxsize=1)
def _graph() -> dict:
    return json.loads(_GRAPH_PATH.read_text(encoding="utf-8"))


_ACCOUNT_TOKENS = {"axium", "baxter", "goya", "texwin", "acquisitions"}


def _norm(text: str) -> str:
    import unicodedata

    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return folded.lower()


def _tokens(title: str) -> list[str]:
    return [token for token in _WORD.findall(_norm(title)) if token not in _GENERIC]


def _forward(node_id: str, edges: list[dict], location_seeds: set[str]) -> list[str]:
    found = []
    for edge in edges:
        if edge["source"] != node_id:
            continue
        if (
            edge["relation"] == "HAS_LOCATION"
            and location_seeds
            and edge["target"] not in location_seeds
        ):
            continue
        found.append(edge["target"])
    return found


def seeds_for(query: str) -> list[dict]:
    """Return ontology nodes named by the question, before any hop."""
    graph = _graph()
    query_l = _norm(query)
    mentioned = {
        token
        for node in graph["nodes"]
        for token in _tokens(node["title"])
        if token in query_l
    }
    place_tokens = mentioned - _ACCOUNT_TOKENS

    def _is_seed(node: dict) -> bool:
        hits = set(_tokens(node["title"])) & mentioned
        article = _norm(node.get("article_id") or "")
        if article and article in query_l:
            hits.add(article)
        if not hits:
            return False
        if node["label"] == "Account":
            return bool(hits & _ACCOUNT_TOKENS)
        if place_tokens and node["label"] in {"Location", "Guideline", "Accumulation"}:
            return bool(hits & place_tokens)
        return True

    return [node for node in graph["nodes"] if _is_seed(node)]


def _account_ids(seeds: list[dict], edges: list[dict]) -> list[str]:
    """Account named in the question, or the owner of a named location."""
    ids = [node["id"] for node in seeds if node["label"] == "Account"]
    locations = {node["id"] for node in seeds if node["label"] == "Location"}
    for edge in edges:
        if edge["relation"] == "HAS_LOCATION" and edge["target"] in locations:
            ids.append(edge["source"])
    seen: list[str] = []
    for account_id in ids:
        if account_id not in seen:
            seen.append(account_id)
    return seen


def _pack(seeds: list[dict], ordered: list[dict], edges: list[dict]) -> dict:
    return {
        "seeds": [node["title"] for node in seeds],
        "nodes": [
            {
                "id": node["id"],
                "label": node["label"],
                "title": node["title"],
                "datastore": node["datastore"],
                "article_id": node.get("article_id") or "",
                "excerpt": node.get("excerpt") or "",
            }
            for node in ordered[:20]
        ],
        "edges": [
            {"source": edge["source"], "relation": edge["relation"], "target": edge["target"]}
            for edge in edges
        ],
    }


def _expand_accounts_json(account_ids: list[str], graph: dict) -> tuple[list[dict], list[dict]]:
    """Forward 2 hops from the account. Do not walk a shared guideline to another book."""
    nodes = {node["id"]: node for node in graph["nodes"]}
    kept = {account_id for account_id in account_ids if account_id in nodes}
    frontier = set(kept)
    for _ in range(2):
        nxt: set[str] = set()
        for node_id in frontier:
            node = nodes.get(node_id)
            if not node or node["label"] == "Guideline":
                continue
            for edge in graph["edges"]:
                if edge["source"] != node_id:
                    continue
                other = edge["target"]
                if other not in kept and other in nodes:
                    kept.add(other)
                    nxt.add(other)
        frontier = nxt
    ordered = [nodes[node_id] for node_id in kept]
    edges = [
        edge
        for edge in graph["edges"]
        if edge["source"] in kept and edge["target"] in kept
    ]
    return ordered, edges


def expand_graph(query: str, tool_context: ToolContext | None = None) -> dict:
    """Load the account neighborhood for this question into the session.

    First turn: resolve the named insured (or the owner of a named location),
    walk Neo4j (or graph.json), and merge the full account into the session
    context graph so later turns can answer without retrieving again.

    Args:
        query: The user question, or a short entity name such as a city or insured.
    """
    graph = _graph()
    nodes = {node["id"]: node for node in graph["nodes"]}
    seeds = seeds_for(query)
    if not seeds:
        return {
            "seeds": [],
            "nodes": [],
            "edges": [],
            "accounts": [node["title"] for node in graph["nodes"] if node["label"] == "Account"],
            "note": "No ontology node matched. Search the accounts datastore with the insured name.",
        }

    account_ids = _account_ids(seeds, graph["edges"])
    if account_ids:
        try:
            from .neo4j_store import fetch_account, is_available

            if is_available():
                merged_nodes: dict[str, dict] = {}
                merged_edges: list[dict] = []
                seen_edge: set[tuple[str, str, str]] = set()
                for account_id in account_ids:
                    part = fetch_account(account_id)
                    for node in part["nodes"]:
                        merged_nodes[node["id"]] = node
                    for edge in part["edges"]:
                        key = (edge["source"], edge["relation"], edge["target"])
                        if key in seen_edge:
                            continue
                        seen_edge.add(key)
                        merged_edges.append(edge)
                result = _pack(seeds, list(merged_nodes.values()), merged_edges)
                result["source"] = "neo4j"
                if tool_context is not None:
                    from .context_graph import remember_expansion

                    remember_expansion(tool_context, result)
                return result
        except Exception:
            pass
        ordered, edges = _expand_accounts_json(account_ids, graph)
        result = _pack(seeds, ordered, edges)
        result["source"] = "graph.json"
        if tool_context is not None:
            from .context_graph import remember_expansion

            remember_expansion(tool_context, result)
        return result

    location_seeds = {node["id"] for node in seeds if node["label"] == "Location"}
    edges = graph["edges"]
    kept = {node["id"] for node in seeds}

    for seed in seeds:
        for edge in edges:
            if edge["target"] != seed["id"]:
                continue
            if seed["label"] == "Guideline" and edge["relation"] in {"GOVERNED_BY", "CAPPED_BY"}:
                kept.add(edge["source"])
            elif seed["label"] == "Accumulation" and edge["relation"] == "COUNTS_TOWARD":
                kept.add(edge["source"])

    frontier = set(kept)
    for _ in range(2):
        nxt: set[str] = set()
        for node_id in frontier:
            for other in _forward(node_id, edges, location_seeds):
                if other not in kept and other in nodes:
                    kept.add(other)
                    nxt.add(other)
        frontier = nxt

    edges = [
        edge
        for edge in graph["edges"]
        if edge["source"] in kept and edge["target"] in kept
    ]
    ordered = [nodes[node_id] for node_id in kept]
    result = _pack(seeds, ordered, edges)
    result["source"] = "graph.json"
    if tool_context is not None:
        from .context_graph import remember_expansion

        remember_expansion(tool_context, result)
    return result
