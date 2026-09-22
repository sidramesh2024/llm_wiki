"""Walk the underwriting ontology shipped with the agent."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

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


def expand_graph(query: str) -> dict:
    """Expand the underwriting ontology around entities named in the question.

    Returns matching Account, Location, Accumulation, and Guideline nodes,
    the typed edges between them, and a short excerpt of each document.
    A named location does not pull sibling sites or other accounts that share
    a guideline.

    Args:
        query: The user question, or a short entity name such as a city or insured.
    """
    graph = _graph()
    nodes = {node["id"]: node for node in graph["nodes"]}
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

    seeds = [node for node in graph["nodes"] if _is_seed(node)]
    if not seeds:
        return {
            "seeds": [],
            "nodes": [],
            "edges": [],
            "accounts": [node["title"] for node in graph["nodes"] if node["label"] == "Account"],
            "note": "No ontology node matched. Search the accounts datastore with the insured name.",
        }

    location_seeds = {node["id"] for node in seeds if node["label"] == "Location"}
    edges = graph["edges"]
    kept = {node["id"] for node in seeds}

    # One reverse step from a seed only: location to its account, or a named
    # guideline back to the nodes it governs. Further hops are forward.
    for seed in seeds:
        for edge in edges:
            if edge["target"] != seed["id"]:
                continue
            if seed["label"] == "Location" and edge["relation"] == "HAS_LOCATION":
                kept.add(edge["source"])
            elif seed["label"] == "Guideline" and edge["relation"] in {"GOVERNED_BY", "CAPPED_BY"}:
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
            for node in ordered[:14]
        ],
        "edges": [
            {"source": edge["source"], "relation": edge["relation"], "target": edge["target"]}
            for edge in edges
        ],
    }
