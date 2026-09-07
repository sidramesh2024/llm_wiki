"""Build an LLM-wiki style knowledge graph from the OKF bundle.

Writes:
  graph/graph.json   nodes + edges
  graph/graph.svg    static force layout for README / GitHub
  graph/graph.html   interactive vis-network viewer (open locally)
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adk_underwriting"))

from tools import load_bundle  # noqa: E402

OUT = ROOT / "graph"

TYPE_COLOR = {
    "Company": "#2E79B5",
    "IKE Guideline": "#7B64B8",
    "Exposure": "#C06028",
    "UW Snapshot": "#1F8A65",
}

TYPE_GROUP = {
    "Company": 1,
    "IKE Guideline": 2,
    "Exposure": 3,
    "UW Snapshot": 4,
}


def short_label(concept: dict) -> str:
    title = concept["title"]
    cid = concept["id"]
    if cid.startswith("ike/") and concept.get("article_id"):
        rest = title.split(" ", 1)[-1]
        return f"{concept['article_id'].replace('IKE-UW-', 'IKE-')} {rest}"[:36]
    if cid.startswith("companies/"):
        return title.replace(" Inc", "").replace(" LLC", "").replace("Acquisitions", "Acq.")
    if cid.startswith("syntheses/"):
        return title.replace(" underwriting snapshot", "").replace(" Inc", "") + " snapshot"
    return title[:36]


def collect_graph(include_snapshots: bool) -> tuple[list[dict], list[dict]]:
    bundle = load_bundle()
    nodes = []
    for cid, c in bundle.items():
        if not include_snapshots and c["type"] == "UW Snapshot":
            continue
        nodes.append(
            {
                "id": cid,
                "label": short_label(c),
                "title": c["title"],
                "type": c["type"],
                "description": c["description"],
                "path": c["path"],
            }
        )
    allowed = {n["id"] for n in nodes}
    seen = set()
    edges = []
    for cid, c in bundle.items():
        if cid not in allowed:
            continue
        for dst in c["links"]:
            if dst not in allowed or dst == cid:
                continue
            key = tuple(sorted((cid, dst)))
            if key in seen:
                continue
            seen.add(key)
            edges.append({"from": cid, "to": dst})
    degree: dict[str, int] = {n["id"]: 0 for n in nodes}
    for e in edges:
        degree[e["from"]] += 1
        degree[e["to"]] += 1
    for n in nodes:
        n["degree"] = degree[n["id"]]
        n["size"] = 12 + 4 * math.sqrt(max(n["degree"], 1))
        n["color"] = TYPE_COLOR.get(n["type"], "#888888")
        n["group"] = TYPE_GROUP.get(n["type"], 0)
    return nodes, edges


def force_layout(
    nodes: list[dict],
    edges: list[dict],
    width: int,
    height: int,
    seed: int = 7,
) -> dict[str, tuple[float, float]]:
    rng = random.Random(seed)
    pos: dict[str, list[float]] = {}
    cx, cy = width / 2, height / 2
    seeds = {
        "Company": (cx, cy),
        "IKE Guideline": (cx, height * 0.22),
        "Exposure": (cx, height * 0.78),
        "UW Snapshot": (cx, cy + 40),
    }
    for n in nodes:
        sx, sy = seeds.get(n["type"], (cx, cy))
        pos[n["id"]] = [sx + rng.uniform(-180, 180), sy + rng.uniform(-50, 50)]

    adj = {n["id"]: set() for n in nodes}
    for e in edges:
        adj[e["from"]].add(e["to"])
        adj[e["to"]].add(e["from"])

    ids = [n["id"] for n in nodes]
    for _ in range(280):
        disp = {i: [0.0, 0.0] for i in ids}
        for i, a in enumerate(ids):
            ax, ay = pos[a]
            for b in ids[i + 1 :]:
                bx, by = pos[b]
                dx, dy = ax - bx, ay - by
                dist = math.hypot(dx, dy) or 0.01
                force = 9000 / (dist * dist)
                ux, uy = dx / dist, dy / dist
                disp[a][0] += ux * force
                disp[a][1] += uy * force
                disp[b][0] -= ux * force
                disp[b][1] -= uy * force
        for e in edges:
            ax, ay = pos[e["from"]]
            bx, by = pos[e["to"]]
            dx, dy = bx - ax, by - ay
            dist = math.hypot(dx, dy) or 0.01
            force = (dist - 140) * 0.045
            ux, uy = dx / dist, dy / dist
            disp[e["from"]][0] += ux * force
            disp[e["from"]][1] += uy * force
            disp[e["to"]][0] -= ux * force
            disp[e["to"]][1] -= uy * force
        for i in ids:
            dx, dy = cx - pos[i][0], cy - pos[i][1]
            disp[i][0] += dx * 0.01
            disp[i][1] += dy * 0.01
            pos[i][0] += max(-18, min(18, disp[i][0]))
            pos[i][1] += max(-18, min(18, disp[i][1]))
            pos[i][0] = min(width - 40, max(40, pos[i][0]))
            pos[i][1] = min(height - 28, max(28, pos[i][1]))
    return {i: (pos[i][0], pos[i][1]) for i in ids}


def write_svg(nodes: list[dict], edges: list[dict], path: Path) -> None:
    width, height = 1100, 720
    layout = force_layout(nodes, edges, width, height - 48)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        'aria-label="LLM wiki knowledge graph of accounts, IKE guidelines, and exposure">'
        f'<rect width="{width}" height="{height}" fill="#f6f5f2"/>',
        '<text x="24" y="28" font-family="ui-sans-serif, system-ui, sans-serif" '
        'font-size="16" font-weight="600" fill="#1a1a1a">Accounts × IKE × Exposure</text>',
        '<text x="24" y="46" font-family="ui-sans-serif, system-ui, sans-serif" '
        'font-size="11" fill="#5c5c5c">Nodes are wiki pages. Edges are markdown links. '
        "Size scales with degree.</text>",
    ]
    legend_x = 720
    for i, (label, color) in enumerate(
        [
            ("Account (Company)", TYPE_COLOR["Company"]),
            ("IKE guideline", TYPE_COLOR["IKE Guideline"]),
            ("Exposure", TYPE_COLOR["Exposure"]),
        ]
    ):
        x = legend_x + i * 125
        parts.append(
            f'<circle cx="{x}" cy="36" r="6" fill="{color}"/>'
            f'<text x="{x + 12}" y="40" font-family="ui-sans-serif, system-ui, sans-serif" '
            f'font-size="11" fill="#1a1a1a">{label}</text>'
        )
    for e in edges:
        x1, y1 = layout[e["from"]]
        x2, y2 = layout[e["to"]]
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            'stroke="#b9b6ae" stroke-width="1.2" opacity="0.85"/>'
        )
    for n in sorted(nodes, key=lambda x: x["degree"]):
        x, y = layout[n["id"]]
        r = 7 + math.sqrt(n["degree"]) * 2.4
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{n["color"]}" '
            'stroke="#f6f5f2" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{y + r + 12:.1f}" text-anchor="middle" '
            'font-family="ui-sans-serif, system-ui, sans-serif" font-size="10" fill="#1a1a1a">'
            f'{_xml(n["label"])}</text>'
        )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_html(nodes: list[dict], edges: list[dict], path: Path) -> None:
    payload = json.dumps({"nodes": nodes, "edges": edges})
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>llm_wiki graph — accounts, IKE, exposure</title>
  <script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
  <style>
    html, body {{ margin: 0; height: 100%; font-family: ui-sans-serif, system-ui, sans-serif; background: #111; color: #eee; }}
    #bar {{ display: flex; gap: 16px; align-items: center; padding: 10px 16px; border-bottom: 1px solid #333; flex-wrap: wrap; }}
    #graph {{ height: calc(100% - 120px); }}
    #detail {{ padding: 8px 16px; font-size: 13px; color: #bbb; min-height: 48px; border-top: 1px solid #333; }}
    .swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }}
    input[type="search"] {{ background: #1c1c1c; color: #eee; border: 1px solid #444; padding: 4px 8px; border-radius: 4px; }}
    label {{ font-size: 12px; color: #ccc; }}
  </style>
</head>
<body>
  <div id="bar">
    <strong>llm_wiki graph</strong>
    <span><span class="swatch" style="background:#2E79B5"></span>Account</span>
    <span><span class="swatch" style="background:#7B64B8"></span>IKE</span>
    <span><span class="swatch" style="background:#C06028"></span>Exposure</span>
    <span><span class="swatch" style="background:#1F8A65"></span>Snapshot</span>
    <input id="q" type="search" placeholder="Find a page…"/>
    <label><input id="snap" type="checkbox"/> snapshots</label>
  </div>
  <div id="graph"></div>
  <div id="detail">Click a node. Hover dims non-neighbors. Node size is link count.</div>
  <script>
    const DATA = {payload};
    const container = document.getElementById("graph");
    const detail = document.getElementById("detail");
    let network;

    function visNodes(includeSnap) {{
      return DATA.nodes
        .filter(n => includeSnap || n.type !== "UW Snapshot")
        .map(n => ({{
          id: n.id,
          label: n.label,
          title: n.title + "\\n" + n.type,
          value: n.degree,
          color: {{ background: n.color, border: n.color }},
          font: {{ color: "#eee", size: 12 }},
          type: n.type,
          description: n.description,
          path: n.path
        }}));
    }}

    function visEdges(includeSnap) {{
      const ids = new Set(visNodes(includeSnap).map(n => n.id));
      return DATA.edges
        .filter(e => ids.has(e.from) && ids.has(e.to))
        .map(e => ({{ from: e.from, to: e.to, color: {{ color: "#555" }}, width: 1 }}));
    }}

    function draw() {{
      const includeSnap = document.getElementById("snap").checked;
      const nodes = new vis.DataSet(visNodes(includeSnap));
      const edges = new vis.DataSet(visEdges(includeSnap));
      if (network) network.destroy();
      network = new vis.Network(container, {{ nodes, edges }}, {{
        nodes: {{ shape: "dot", scaling: {{ min: 10, max: 32 }} }},
        edges: {{ smooth: {{ type: "continuous" }} }},
        physics: {{
          solver: "forceAtlas2Based",
          forceAtlas2Based: {{ gravitationalConstant: -50, springLength: 120, springConstant: 0.08 }},
          stabilization: {{ iterations: 200 }}
        }},
        interaction: {{ hover: true, tooltipDelay: 80 }}
      }});
      network.on("click", params => {{
        if (!params.nodes.length) return;
        const n = DATA.nodes.find(x => x.id === params.nodes[0]);
        detail.textContent = n.path + " · " + n.type + " — " + (n.description || n.title);
      }});
    }}

    document.getElementById("snap").addEventListener("change", draw);
    document.getElementById("q").addEventListener("input", ev => {{
      const q = ev.target.value.toLowerCase();
      if (!network) return;
      const hits = DATA.nodes.filter(n => n.label.toLowerCase().includes(q) || n.title.toLowerCase().includes(q));
      if (q && hits.length) network.selectNodes([hits[0].id]);
    }});
    draw();
  </script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    svg_nodes, svg_edges = collect_graph(include_snapshots=False)
    all_nodes, all_edges = collect_graph(include_snapshots=True)
    (OUT / "graph.json").write_text(
        json.dumps({"nodes": all_nodes, "edges": all_edges}, indent=2),
        encoding="utf-8",
    )
    write_svg(svg_nodes, svg_edges, OUT / "graph.svg")
    write_html(all_nodes, all_edges, OUT / "graph.html")
    print(f"wrote {len(all_nodes)} nodes, {len(all_edges)} edges -> {OUT}")


if __name__ == "__main__":
    main()
