"""Load the local OKF bundle and expose graph + mock-datastore tools."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

OKF_ROOT = Path(__file__).resolve().parents[1] / "okf"

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    match = _FM_RE.match(raw)
    if not match:
        return {}, raw.strip()
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("-"):
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta, match.group(2).strip()


def _concept_id(path: Path) -> str:
    return path.relative_to(OKF_ROOT).with_suffix("").as_posix()


@lru_cache(maxsize=1)
def load_bundle() -> dict[str, dict]:
    concepts: dict[str, dict] = {}
    for path in OKF_ROOT.rglob("*.md"):
        if path.name in {"index.md", "log.md"}:
            continue
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        cid = _concept_id(path)
        links: list[str] = []
        for _label, href in _LINK_RE.findall(body):
            if href.startswith("http") or href.startswith("#"):
                continue
            target = (path.parent / href).resolve()
            try:
                links.append(_concept_id(target))
            except ValueError:
                continue
        concepts[cid] = {
            "id": cid,
            "path": str(path.relative_to(OKF_ROOT)),
            "type": meta.get("type", "Concept"),
            "title": meta.get("title", path.stem),
            "description": meta.get("description", ""),
            "article_id": meta.get("article_id", ""),
            "body": body,
            "links": sorted(set(links)),
        }
    return concepts


def _search_companies(query: str) -> list[dict]:
    q = query.lower()
    hits = []
    for concept in load_bundle().values():
        if concept["type"] != "Company":
            continue
        blob = f"{concept['title']} {concept['id']} {concept['description']}".lower()
        if q in blob or all(part in blob for part in q.split()):
            hits.append(concept)
    return hits


def okf_resolve_company(company_name: str) -> dict:
    """Find a Company concept and return linked IKE, exposure, and snapshot IDs.

    Call this first on the router. The returned IDs are what you pass to
    the Company, IKE, and Exposure sub-agents.
    """
    hits = _search_companies(company_name)
    if not hits:
        known = [c["title"] for c in load_bundle().values() if c["type"] == "Company"]
        return {"status": "not_found", "query": company_name, "known_companies": known}
    company = hits[0]
    bundle = load_bundle()
    ike, exposure, snapshots = [], [], []
    for link in company["links"]:
        linked = bundle.get(link)
        if not linked:
            continue
        if linked["type"] == "IKE Guideline":
            ike.append({"id": link, "title": linked["title"]})
        elif linked["type"] == "Exposure":
            exposure.append({"id": link, "title": linked["title"]})
        elif linked["type"] == "UW Snapshot":
            snapshots.append({"id": link, "title": linked["title"]})
    return {
        "status": "ok",
        "company_id": company["id"],
        "title": company["title"],
        "snapshot_ids": snapshots,
        "ike_ids": ike,
        "exposure_ids": exposure,
        "instruction": (
            "Next: call company_agent with company_id, ike_agent with ike_ids, "
            "and exposure_agent with exposure_ids. Do not skip a specialist."
        ),
    }


def okf_read_snapshot(snapshot_id: str) -> dict:
    """Read a compiled UW snapshot page from the OKF bundle."""
    concept = load_bundle().get(snapshot_id)
    if not concept:
        return {"status": "not_found", "id": snapshot_id}
    return {
        "status": "ok",
        "id": concept["id"],
        "title": concept["title"],
        "type": concept["type"],
        "body": concept["body"],
    }


def get_company_profile(company_id: str) -> dict:
    """Mock Companies datastore: return the Company OKF page.

    Production: replace with VertexAiSearchTool on the companies datastore,
    filtered to this named insured.
    """
    concept = load_bundle().get(company_id)
    if not concept or concept["type"] != "Company":
        return {"status": "not_found", "id": company_id, "expected_type": "Company"}
    return {
        "status": "ok",
        "datastore": "companies (mock)",
        "id": concept["id"],
        "title": concept["title"],
        "body": concept["body"],
    }


def get_ike_articles(ike_ids: str) -> dict:
    """Mock IKE datastore: fetch one or more guideline articles.

    ike_ids: comma-separated concept ids such as
    'ike/occupancy-food-processing,ike/hazard-cooking-fryer'
    """
    bundle = load_bundle()
    articles = []
    missing = []
    for raw_id in [part.strip() for part in ike_ids.split(",") if part.strip()]:
        concept = bundle.get(raw_id)
        if not concept or concept["type"] != "IKE Guideline":
            missing.append(raw_id)
            continue
        articles.append(
            {
                "id": concept["id"],
                "article_id": concept["article_id"],
                "title": concept["title"],
                "body": concept["body"],
            }
        )
    return {
        "status": "ok" if articles else "not_found",
        "datastore": "ike-guidelines (mock)",
        "articles": articles,
        "missing": missing,
    }


def get_exposure_docs(exposure_ids: str) -> dict:
    """Mock Exposure datastore: fetch location and accumulation documents.

    exposure_ids: comma-separated concept ids.
    """
    bundle = load_bundle()
    docs = []
    missing = []
    for raw_id in [part.strip() for part in exposure_ids.split(",") if part.strip()]:
        concept = bundle.get(raw_id)
        if not concept or concept["type"] != "Exposure":
            missing.append(raw_id)
            continue
        docs.append(
            {
                "id": concept["id"],
                "title": concept["title"],
                "body": concept["body"],
            }
        )
    return {
        "status": "ok" if docs else "not_found",
        "datastore": "risk-exposure (mock)",
        "docs": docs,
        "missing": missing,
    }
