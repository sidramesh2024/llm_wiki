"""Build the ontology graph and create three Vertex AI Search datastores."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "adk_underwriting"))

from tools import load_bundle  # noqa: E402
from uw_chat.gcp_config import (  # noqa: E402
    COLLECTION,
    DATA_STORES,
    ENGINE_ID,
    PROJECT_ID,
    SEARCH_LOCATION,
)

BUCKET = f"{PROJECT_ID}-uw-corpus"
GRAPH_PATH = ROOT / "uw_chat" / "graph.json"
CORPUS_DIR = ROOT / "build" / "corpus"
API = "https://discoveryengine.googleapis.com/v1"

REL = {
    ("Account", "Location"): "HAS_LOCATION",
    ("Account", "Guideline"): "GOVERNED_BY",
    ("Location", "Guideline"): "GOVERNED_BY",
    ("Location", "Accumulation"): "COUNTS_TOWARD",
    ("Account", "Accumulation"): "COUNTS_TOWARD",
    ("Accumulation", "Guideline"): "CAPPED_BY",
}

STORE_BY_LABEL = {
    "Account": "accounts",
    "Guideline": "guidelines",
    "Location": "exposures",
    "Accumulation": "exposures",
}

DISPLAY = {
    "uw-accounts": "UW accounts",
    "uw-guidelines": "UW guidelines",
    "uw-exposures": "UW exposures",
}


def _label(concept: dict) -> str:
    cid, typ = concept["id"], concept["type"]
    if typ == "Company":
        return "Account"
    if typ == "IKE Guideline":
        return "Guideline"
    if typ == "Exposure" and cid.startswith("exposure/accum"):
        return "Accumulation"
    if typ == "Exposure":
        return "Location"
    return "Page"


def _edge(src_id: str, src_label: str, dst_id: str, dst_label: str):
    rel = REL.get((src_label, dst_label))
    if rel:
        return src_id, rel, dst_id
    rel = REL.get((dst_label, src_label))
    if rel:
        return dst_id, rel, src_id
    return None


def build_graph(bundle: dict[str, dict]) -> dict:
    nodes = []
    labels = {}
    for concept in bundle.values():
        label = _label(concept)
        if label == "Page":
            continue
        labels[concept["id"]] = label
        nodes.append(
            {
                "id": concept["id"],
                "label": label,
                "title": concept["title"],
                "datastore": STORE_BY_LABEL[label],
                "article_id": concept.get("article_id") or "",
                "excerpt": concept["body"][:900],
            }
        )
    seen: set[tuple[str, str, str]] = set()
    edges = []
    for concept in bundle.values():
        src_label = labels.get(concept["id"])
        if not src_label:
            continue
        for dst_id in concept["links"]:
            dst_label = labels.get(dst_id)
            if not dst_label:
                continue
            edge = _edge(concept["id"], src_label, dst_id, dst_label)
            if not edge or edge in seen:
                continue
            seen.add(edge)
            source, relation, target = edge
            edges.append({"source": source, "relation": relation, "target": target})
    return {"nodes": nodes, "edges": edges}


def _token() -> str:
    return subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()


def _request(method: str, url: str, body: dict | None = None) -> tuple[int, dict | str]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
            "x-goog-user-project": PROJECT_ID,
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        try:
            parsed = json.loads(detail)
        except json.JSONDecodeError:
            parsed = detail
        return exc.code, parsed


def _parent() -> str:
    return (
        f"projects/{PROJECT_ID}/locations/{SEARCH_LOCATION}"
        f"/collections/{COLLECTION}"
    )


def write_corpus(bundle: dict[str, dict], graph: dict) -> None:
    labels = {node["id"]: node for node in graph["nodes"]}
    if CORPUS_DIR.exists():
        for path in CORPUS_DIR.rglob("*"):
            if path.is_file():
                path.unlink()
    for store in ("accounts", "guidelines", "exposures"):
        (CORPUS_DIR / store).mkdir(parents=True, exist_ok=True)
    lines: dict[str, list[str]] = {store: [] for store in lines_keys()}
    for concept in bundle.values():
        node = labels.get(concept["id"])
        if not node:
            continue
        store = node["datastore"]
        doc_id = concept["id"].replace("/", "_")
        text = f"{concept['title']}\nType: {node['label']}\n\n{concept['body']}"
        rel = f"{store}/{doc_id}.txt"
        (CORPUS_DIR / rel).write_text(text, encoding="utf-8")
        document = {
            "id": doc_id,
            "structData": {
                "title": concept["title"],
                "entity_type": node["label"],
                "doc_id": concept["id"],
                "article_id": node["article_id"],
            },
            "content": {
                "mimeType": "text/plain",
                "uri": f"gs://{BUCKET}/{rel}",
            },
        }
        lines[store].append(json.dumps(document))
    for store, docs in lines.items():
        (CORPUS_DIR / f"{store}.jsonl").write_text("\n".join(docs) + "\n", encoding="utf-8")
        print(f"corpus {store}: {len(docs)} documents")


def lines_keys():
    return ("accounts", "guidelines", "exposures")


def ensure_bucket() -> None:
    status = subprocess.run(
        ["gcloud", "storage", "buckets", "describe", f"gs://{BUCKET}", f"--project={PROJECT_ID}"],
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        subprocess.check_call(
            [
                "gcloud",
                "storage",
                "buckets",
                "create",
                f"gs://{BUCKET}",
                "--location=us-central1",
                f"--project={PROJECT_ID}",
                "--uniform-bucket-level-access",
            ]
        )
    subprocess.check_call(
        [
            "gcloud",
            "storage",
            "rsync",
            "--recursive",
            str(CORPUS_DIR),
            f"gs://{BUCKET}",
        ]
    )
    identity = subprocess.run(
        [
            "gcloud",
            "beta",
            "services",
            "identity",
            "create",
            "--service=discoveryengine.googleapis.com",
            f"--project={PROJECT_ID}",
        ],
        capture_output=True,
        text=True,
    )
    print(identity.stdout or identity.stderr)
    number = subprocess.check_output(
        [
            "gcloud",
            "projects",
            "describe",
            PROJECT_ID,
            "--format=value(projectNumber)",
        ],
        text=True,
    ).strip()
    member = f"serviceAccount:service-{number}@gcp-sa-discoveryengine.iam.gserviceaccount.com"
    subprocess.check_call(
        [
            "gcloud",
            "storage",
            "buckets",
            "add-iam-policy-binding",
            f"gs://{BUCKET}",
            f"--member={member}",
            "--role=roles/storage.objectViewer",
        ]
    )


def ensure_datastore(store_id: str) -> None:
    name = f"{_parent()}/dataStores/{store_id}"
    code, body = _request("GET", f"{API}/{name}")
    if code == 200:
        print(f"datastore exists: {store_id}")
        return
    code, body = _request(
        "POST",
        f"{API}/{_parent()}/dataStores?dataStoreId={store_id}",
        {
            "displayName": DISPLAY[store_id],
            "industryVertical": "GENERIC",
            "contentConfig": "CONTENT_REQUIRED",
            "solutionTypes": ["SOLUTION_TYPE_SEARCH"],
            "documentProcessingConfig": {
                "defaultParsingConfig": {"digitalParsingConfig": {}}
            },
        },
    )
    if code not in (200, 409):
        raise SystemExit(f"create datastore {store_id} failed ({code}): {body}")
    print(f"datastore created: {store_id}")
    _wait(body)


def _wait(operation: dict | str, timeout: int = 600) -> dict:
    if not isinstance(operation, dict) or "name" not in operation:
        return {}
    if operation.get("done"):
        return operation
    name = operation["name"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, body = _request("GET", f"{API}/{name}")
        if code != 200 or not isinstance(body, dict):
            raise SystemExit(f"poll {name} failed ({code}): {body}")
        if body.get("done"):
            if body.get("error"):
                raise SystemExit(f"operation {name} failed: {body['error']}")
            print(f"operation done: {name}")
            return body
        time.sleep(5)
    raise SystemExit(f"timed out waiting for {name}")


def import_documents(store: str, store_id: str) -> None:
    code, body = _request(
        "POST",
        f"{API}/{_parent()}/dataStores/{store_id}/branches/default_branch/documents:import",
        {
            "gcsSource": {
                "inputUris": [f"gs://{BUCKET}/{store}.jsonl"],
                "dataSchema": "document",
            },
            "reconciliationMode": "FULL",
        },
    )
    if code != 200:
        raise SystemExit(f"import {store_id} failed ({code}): {body}")
    _wait(body, timeout=900)


def ensure_engine() -> None:
    name = f"{_parent()}/engines/{ENGINE_ID}"
    code, body = _request("GET", f"{API}/{name}")
    if code == 200:
        print(f"engine exists: {ENGINE_ID}")
        return
    code, body = _request(
        "POST",
        f"{API}/{_parent()}/engines?engineId={ENGINE_ID}",
        {
            "displayName": "Underwriting knowledge search",
            "solutionType": "SOLUTION_TYPE_SEARCH",
            "industryVertical": "GENERIC",
            "dataStoreIds": list(DATA_STORES.values()),
            "searchEngineConfig": {"searchTier": "SEARCH_TIER_ENTERPRISE"},
        },
    )
    if code not in (200, 409):
        raise SystemExit(f"create engine failed ({code}): {body}")
    print(f"engine created: {ENGINE_ID}")
    _wait(body)


def main() -> None:
    bundle = load_bundle()
    graph = build_graph(bundle)
    GRAPH_PATH.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    print(
        f"graph: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges -> {GRAPH_PATH}"
    )
    write_corpus(bundle, graph)
    ensure_bucket()
    for store, store_id in DATA_STORES.items():
        ensure_datastore(store_id)
        import_documents(store, store_id)
    ensure_engine()
    print("provisioned", ", ".join(DATA_STORES.values()), "and", ENGINE_ID)


if __name__ == "__main__":
    main()
