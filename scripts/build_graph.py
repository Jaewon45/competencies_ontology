"""Build the public competency graph from the private achievements file.

    python scripts/build_graph.py              # real data: data/achievements.yaml (git-ignored)
    python scripts/build_graph.py --example    # example data: data/achievements.example.yaml

Writes site/graph.json. Only the fields listed in PUBLIC_FIELDS reach that file;
facts and phrasings never leave the private input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = ROOT / "site"

# ---------------------------------------------------------------------------
# Publication policy: the only fields that can appear in site/graph.json.
# ---------------------------------------------------------------------------
PUBLIC_FIELDS = {
    "area": ["id", "type", "label"],
    "skill": ["id", "type", "label", "area", "level", "esco"],
    "role": ["id", "type", "label", "org", "title", "period"],
    "project": ["id", "type", "label", "kind", "period"],
    "achievement": ["id", "type", "label", "evidence", "period", "source_hash"],
}
# Statuses that are published. "needs-check" stays out unless --include-needs-check.
PUBLISHED_STATUSES = {"verified", "user-confirmed"}
VALID_STATUSES = PUBLISHED_STATUSES | {"needs-check"}
VALID_LEVELS = {"proven", "familiar"}


class BuildError(Exception):
    pass


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def short_hash(obj) -> str:
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def validate(ach: dict, skills: dict, block: list[str]) -> list[str]:
    errors: list[str] = []
    evidence_ids = [e["id"] for e in ach.get("roles", []) + ach.get("projects", [])]
    if len(evidence_ids) != len(set(evidence_ids)):
        errors.append("duplicate role/project ids")

    skill_ids = {s["id"] for s in skills["skills"]}
    area_ids = {a["id"] for a in skills["areas"]}
    for s in skills["skills"]:
        if s.get("area") not in area_ids:
            errors.append(f"skill {s['id']}: unknown area {s.get('area')!r}")
        if s.get("level") not in VALID_LEVELS:
            errors.append(f"skill {s['id']}: level must be one of {sorted(VALID_LEVELS)}")

    seen: set[str] = set()
    patterns = [re.compile(p, re.IGNORECASE) for p in block]
    for a in ach.get("achievements", []):
        aid = a.get("id", "<missing id>")
        if aid in seen:
            errors.append(f"duplicate achievement id {aid}")
        seen.add(aid)
        for key in ("id", "label", "evidence", "status", "skills"):
            if key not in a:
                errors.append(f"{aid}: missing field {key!r}")
        if a.get("evidence") not in evidence_ids:
            errors.append(f"{aid}: evidence {a.get('evidence')!r} is not a known role or project")
        if a.get("status") not in VALID_STATUSES:
            errors.append(f"{aid}: status must be one of {sorted(VALID_STATUSES)}")
        for sid in a.get("skills") or []:
            if sid not in skill_ids:
                errors.append(f"{aid}: skill {sid!r} is not in data/skills.yaml")
        texts = [a.get("label", "")] + list((a.get("phrasings") or {}).values())
        for p in patterns:
            for t in texts:
                if p.search(t or ""):
                    errors.append(f"{aid}: blocked claim matches /{p.pattern}/")
    return errors


def build(ach: dict, skills: dict, input_path: Path, include_needs_check: bool) -> dict:
    statuses = PUBLISHED_STATUSES | ({"needs-check"} if include_needs_check else set())
    achievements = [a for a in ach.get("achievements", []) if a["status"] in statuses]
    excluded = [a["id"] for a in ach.get("achievements", []) if a["status"] not in statuses]

    evidence = {}
    for r in ach.get("roles", []):
        evidence[r["id"]] = {"id": r["id"], "type": "role", "label": r["org"], "org": r["org"],
                             "title": r["title"], "period": r.get("period")}
    for p in ach.get("projects", []):
        evidence[p["id"]] = {"id": p["id"], "type": "project", "label": p["title"],
                             "kind": p.get("kind"), "period": p.get("period")}

    used_skills = {sid for a in achievements for sid in a["skills"]}
    nodes, links = [], []

    for area in skills["areas"]:
        nodes.append({"id": "area:" + area["id"], "type": "area", "label": area["label"]})

    for s in skills["skills"]:
        if s["id"] in used_skills or s["level"] == "familiar":
            nodes.append({"id": s["id"], "type": "skill", **{k: s.get(k) for k in ("label", "area", "level", "esco")}})
            links.append({"source": s["id"], "target": "area:" + s["area"], "type": "in_area"})

    used_evidence = {a["evidence"] for a in achievements}
    for eid in sorted(used_evidence):
        nodes.append(evidence[eid])

    for a in achievements:
        nodes.append({
            "id": a["id"], "type": "achievement", "label": a["label"], "evidence": a["evidence"],
            "period": evidence[a["evidence"]].get("period"),
            "source_hash": short_hash(a),   # lineage: changes whenever the private record changes
        })
        links.append({"source": a["id"], "target": a["evidence"], "type": "part_of"})
        for sid in a["skills"]:
            links.append({"source": a["id"], "target": sid, "type": "demonstrates"})

    # Enforce the publication policy on every node.
    public_nodes = [{k: n[k] for k in PUBLIC_FIELDS[n["type"]] if k in n} for n in nodes]

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "input": "example" if "example" in input_path.name else "private",
            "input_hash": file_hash(input_path),
            "skills_hash": file_hash(DATA / "skills.yaml"),
            "counts": {t: sum(1 for n in public_nodes if n["type"] == t) for t in PUBLIC_FIELDS},
            "excluded_achievements": len(excluded),
        },
        "nodes": public_nodes,
        "links": links,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--example", action="store_true", help="use data/achievements.example.yaml")
    ap.add_argument("--include-needs-check", action="store_true", help="also publish achievements marked needs-check")
    ap.add_argument("--out", type=Path, default=SITE / "graph.json")
    args = ap.parse_args()

    input_path = DATA / ("achievements.example.yaml" if args.example else "achievements.yaml")
    if not input_path.exists():
        print(f"error: {input_path.relative_to(ROOT)} not found (use --example to build from the example file)", file=sys.stderr)
        return 1

    ach = load_yaml(input_path)
    skills = load_yaml(DATA / "skills.yaml")
    corrections_path = DATA / "corrections.private.yaml"
    block = load_yaml(corrections_path).get("block", []) if corrections_path.exists() else []

    errors = validate(ach, skills, block)
    if errors:
        print("Build stopped. Fix these first:", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1

    graph = build(ach, skills, input_path, args.include_needs_check)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    c = graph["meta"]["counts"]
    print(f"Wrote {args.out.relative_to(ROOT)}: {c['skill']} skills, {c['achievement']} achievements, "
          f"{c['role'] + c['project']} roles/projects ({graph['meta']['excluded_achievements']} excluded as needs-check)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
