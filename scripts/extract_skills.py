"""Propose skills for achievements with a small local LLM (Ollama), and evaluate how good the proposals are.

Proposals are a supplementary suggestion step: nothing reaches data/achievements.yaml or the public graph
until a person reviews and copies it.

    ollama pull gemma3:4b
    python scripts/extract_skills.py --model gemma3:4b               # propose for achievements without skills
    python scripts/extract_skills.py --model gemma3:4b --ids kr-data  # specific achievements
    python scripts/extract_skills.py --model gemma3:4b --dry-run      # print the prompts, call nothing
    python scripts/extract_skills.py --eval --models gemma3:4b qwen2.5:3b llama3.2:3b
    python scripts/extract_skills.py --bootstrap-only                 # after --eval: intervals from saved results

How a proposal is made (two calls per achievement, all on this machine):
  1. Area step: the model picks 1 to 3 competency areas the achievement clearly shows.
  2. Skill step: the model picks skills only from those areas (closed vocabulary, JSON schema),
     and must quote the phrase from the input that proves each one.
  3. Grounding check: a skill whose quote is not found in the input text is dropped.

Evaluation compares proposals with the skills already assigned by hand in data/achievements.yaml
(the few-shot examples are excluded from the test set). Per-item results go to data/eval.private.yaml
(git-ignored); public metrics and run metadata go to eval/results.json.

--bootstrap-only skips extraction and calls no model. It reads data/eval.private.yaml and writes
  eval/bootstrap.json       95% percentile intervals (resampling achievements), strict and lenient,
                            plus paired differences between models
  eval/near_misses.csv      one row per near-miss: model, achievement_id, gold_skill, category, chosen_skill
  eval/error_patterns.json  near-miss counts, the rules used, most-missed and most wrongly proposed skills
  data/near_misses.private.yaml  the same cases with quoted evidence (git-ignored)
Public files hold skill and achievement ids only, never source text.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import subprocess
import sys
import time
from collections import Counter
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PROPOSALS = DATA / "proposals.private.yaml"
EVAL_PRIVATE = DATA / "eval.private.yaml"
EVAL_DIR = ROOT / "eval"

# Achievements used as worked examples in the prompt. They are excluded from evaluation.
FEWSHOT = [
    {
        "id": "ba-forecasting",
        "answer": {
            "skills": [
                {"id": "time-series-forecasting", "evidence": "ARIMA, Prophet, N-BEATS"},
                {"id": "hierarchical-forecasting", "evidence": "hierarchical reconciliation"},
                {"id": "hyperparameter-tuning", "evidence": "Optuna hyperparameter optimization"},
            ],
            "new_skills": [],
        },
    },
    {
        "id": "sa-kpi",
        "answer": {
            "skills": [
                {"id": "data-integration", "evidence": "integrating clinical, HCP-behavior and Google Analytics data"},
                {"id": "regulated-pharma", "evidence": "EMA/FDA-regulated"},
                {"id": "google-analytics", "evidence": "Google Analytics metrics"},
            ],
            "new_skills": [],
        },
    },
]
# A made-up example that teaches the model to return no catalogue skill when none fits.
SYNTHETIC_EXAMPLE = {
    "text": "Label: Assay protocol work\nFacts:\nwhat: optimized ELISA protocols for a wet lab\n"
            "Resume wording: Optimized ELISA protocols, cutting assay time by a third.",
    "answer": {"skills": [], "new_skills": [{"label": "Laboratory assay development", "evidence": "Optimized ELISA protocols"}]},
}

AREA_SYSTEM = """You classify one resume achievement into competency areas.
Pick 1 to 3 areas that the achievement text clearly shows. Pick fewer when unsure.
Areas (id: what it covers):
{areas}"""

SKILL_SYSTEM = """You map one resume achievement to skills from a fixed catalogue.
Rules:
- Choose only skill ids from the catalogue below.
- Choose a skill only if the text clearly shows the person used it. Do not guess from job titles or context.
- It is fine to return no skills. Fewer is better than wrong.
- For each skill, copy the exact phrase from the text that proves it into "evidence".
- If the text clearly shows an important skill that is not in the catalogue, list it under new_skills.
Catalogue (id: label):
{catalogue}"""


# ---------------------------------------------------------------- helpers


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.exists() else None


def run_metadata(host: str, models: list[str]) -> dict:
    """Provenance for a run: exact model digests, code version and input hashes."""
    digests = {}
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=10) as resp:
            tags = json.loads(resp.read().decode("utf-8")).get("models", [])
        digests = {t["name"]: t.get("digest", "")[:12] for t in tags if t.get("name") in models}
    except (urllib.error.URLError, json.JSONDecodeError, KeyError):
        pass
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or None
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "scripts"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    except OSError:
        commit, dirty = None, None
    return {"models": [{"name": m, "digest": digests.get(m)} for m in models],
            "git_commit": commit, "scripts_modified_since_commit": dirty,
            "input_hashes": {"achievements": file_hash(DATA / "achievements.yaml"), "skills": file_hash(DATA / "skills.yaml")}}

def norm(s: str) -> str:
    s = s.lower().replace("’", "'")
    s = re.sub(r"[^\w%/+.\- ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def achievement_text(a: dict) -> str:
    parts = [f"Label: {a.get('label', '')}"]
    if a.get("facts"):
        parts.append("Facts:\n" + yaml.safe_dump(a["facts"], allow_unicode=True, sort_keys=False, width=1000).strip())
    default = (a.get("phrasings") or {}).get("default")
    if default:
        parts.append(f"Resume wording: {' '.join(default.split())}")
    return "\n".join(parts)


def chat(host: str, model: str, messages: list[dict], fmt: dict, timeout: int) -> tuple[dict | None, float]:
    body = json.dumps({"model": model, "messages": messages, "format": fmt, "stream": False,
                       "options": {"temperature": 0, "seed": 42}}).encode("utf-8")
    req = urllib.request.Request(f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SystemExit(f"error: model {model!r} is not installed. Run: ollama pull {model}")
        raise
    except urllib.error.URLError as e:
        raise SystemExit(f"error: cannot reach Ollama at {host} ({e.reason}). Start it with: brew services start ollama")
    elapsed = time.perf_counter() - t0
    try:
        return json.loads(payload["message"]["content"]), elapsed
    except (KeyError, json.JSONDecodeError):
        return None, elapsed


class Extractor:
    def __init__(self, skills_doc: dict, achievements: list[dict], host: str, timeout: int):
        self.areas = skills_doc["areas"]
        self.skills = skills_doc["skills"]
        self.by_area = {a["id"]: [s for s in self.skills if s["area"] == a["id"]] for a in self.areas}
        self.host, self.timeout = host, timeout
        by_id = {a["id"]: a for a in achievements}
        self.examples = [(achievement_text(by_id[f["id"]]), f["answer"]) for f in FEWSHOT if f["id"] in by_id]
        self.examples.append((SYNTHETIC_EXAMPLE["text"], SYNTHETIC_EXAMPLE["answer"]))
        self.fewshot_ids = {f["id"] for f in FEWSHOT}

    def area_prompt(self) -> str:
        lines = [f"{a['id']}: {a['label']} (e.g. {', '.join(s['label'] for s in self.by_area[a['id']][:8])})" for a in self.areas]
        return AREA_SYSTEM.format(areas="\n".join(lines))

    def skill_prompt(self, area_ids: list[str]) -> tuple[str, list[str]]:
        cand = [s for aid in area_ids for s in self.by_area.get(aid, [])]
        # Example answers must stay valid under the schema, so their skills are always offered.
        ex_ids = {s["id"] for _, ans in self.examples for s in ans["skills"]}
        cand += [s for s in self.skills if s["id"] in ex_ids and s not in cand]
        return SKILL_SYSTEM.format(catalogue="\n".join(f"{s['id']}: {s['label']}" for s in cand)), [s["id"] for s in cand]

    def propose(self, model: str, text: str) -> dict:
        area_ids = [a["id"] for a in self.areas]
        area_fmt = {"type": "object", "required": ["areas"], "properties": {
            "areas": {"type": "array", "maxItems": 3, "items": {"type": "string", "enum": area_ids}}}}
        areas_out, t1 = chat(self.host, model, [{"role": "system", "content": self.area_prompt()},
                                                {"role": "user", "content": text}], area_fmt, self.timeout)
        chosen = [a for a in (areas_out or {}).get("areas", []) if a in area_ids][:3] or area_ids

        system, cand_ids = self.skill_prompt(chosen)
        skill_fmt = {"type": "object", "required": ["skills", "new_skills"], "properties": {
            "skills": {"type": "array", "items": {"type": "object", "required": ["id", "evidence"], "properties": {
                "id": {"type": "string", "enum": cand_ids}, "evidence": {"type": "string"}}}},
            "new_skills": {"type": "array", "items": {"type": "object", "required": ["label", "evidence"], "properties": {
                "label": {"type": "string"}, "evidence": {"type": "string"}}}}}}
        messages = [{"role": "system", "content": system}]
        for ex_text, ex_ans in self.examples:
            messages += [{"role": "user", "content": ex_text}, {"role": "assistant", "content": json.dumps(ex_ans)}]
        messages.append({"role": "user", "content": text})
        out, t2 = chat(self.host, model, messages, skill_fmt, self.timeout)

        ntext = norm(text)
        kept, ungrounded, invalid, seen = [], [], 0, set()
        for s in (out or {}).get("skills", []):
            sid, ev = s.get("id"), s.get("evidence", "")
            if sid not in cand_ids:
                invalid += 1
            elif sid in seen:
                continue
            elif len(norm(ev)) >= 3 and norm(ev) in ntext:
                kept.append({"id": sid, "evidence": ev}); seen.add(sid)
            else:
                ungrounded.append({"id": sid, "evidence": ev})
        return {"areas": chosen, "skills": kept, "dropped_ungrounded": ungrounded, "invalid_ids": invalid,
                "new_skills": (out or {}).get("new_skills", []), "parse_failed": out is None, "seconds": round(t1 + t2, 2)}


# ---------------------------------------------------------------- modes

def run_propose(ex: Extractor, items: list[dict], model: str) -> None:
    existing = yaml.safe_load(PROPOSALS.read_text(encoding="utf-8")) if PROPOSALS.exists() else None
    proposals = (existing or {}).get("proposals", {})
    for a in items:
        text = achievement_text(a)
        r = ex.propose(model, text)
        proposals[a["id"]] = {
            "status": "pending",  # change to accepted / rejected after review
            "model": model,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "input_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
            "current_skills": a.get("skills") or [],
            "proposed_skills": r["skills"],
            "dropped_ungrounded": r["dropped_ungrounded"],
            "new_skills": r["new_skills"],
        }
        extra = f" (+{len(r['new_skills'])} new, {len(r['dropped_ungrounded'])} ungrounded dropped)"
        print(f"{a['id']}: {', '.join(s['id'] for s in r['skills']) or 'no catalogue skills'}{extra}")
    PROPOSALS.write_text("# PRIVATE: git-ignored. LLM skill proposals awaiting review.\n"
                         + yaml.safe_dump({"proposals": proposals}, allow_unicode=True, sort_keys=False, width=110),
                         encoding="utf-8")
    print(f"Saved to {PROPOSALS.relative_to(ROOT)}. Review it, then copy accepted ids into data/achievements.yaml.")


def run_eval(ex: Extractor, items: list[dict], models: list[str]) -> None:
    test = [a for a in items if a.get("skills") and a["id"] not in ex.fewshot_ids]
    if not test:
        raise SystemExit("error: no hand-labelled achievements to evaluate against")
    detail, rows = {}, []
    for model in models:
        tp = fp = fn = ungrounded = invalid = failed = 0
        secs, per_item = [], {}
        print(f"\n{model} on {len(test)} achievements")
        for a in test:
            r = ex.propose(model, achievement_text(a))
            gold, pred = set(a["skills"]), {s["id"] for s in r["skills"]}
            tp += len(gold & pred); fp += len(pred - gold); fn += len(gold - pred)
            ungrounded += len(r["dropped_ungrounded"]); invalid += r["invalid_ids"]; failed += r["parse_failed"]
            secs.append(r["seconds"])
            per_item[a["id"]] = {"gold": sorted(gold), "predicted": sorted(pred), "correct": sorted(gold & pred),
                                 "wrong": sorted(pred - gold), "missed": sorted(gold - pred), "areas": r["areas"],
                                 "dropped_ungrounded": r["dropped_ungrounded"], "new_skills": r["new_skills"]}
            print(f"  {a['id']:<26} correct {len(gold & pred)}  wrong {len(pred - gold)}  missed {len(gold - pred)}")
        p = tp / (tp + fp) if tp + fp else 0.0
        rcl = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * p * rcl / (p + rcl) if p + rcl else 0.0
        rows.append({"model": model, "precision": p, "recall": rcl, "f1": f1, "ungrounded": ungrounded,
                     "invalid": invalid, "failed": failed, "sec": sum(secs) / len(secs)})
        detail[model] = per_item

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = run_metadata(ex.host, models)
    meta.update({"generated_at": stamp, "n_achievements": len(test), "temperature": 0, "seed": 42,
                 "few_shot_excluded": sorted(ex.fewshot_ids)})
    EVAL_PRIVATE.write_text("# PRIVATE: git-ignored. Per-achievement evaluation results.\n"
                            + yaml.safe_dump({"generated_at": stamp, "meta": meta, "models": detail},
                                             allow_unicode=True, sort_keys=False, width=110),
                            encoding="utf-8")

    results = {"meta": meta, "models": [
        {"model": r["model"], "precision": round(r["precision"], 4), "recall": round(r["recall"], 4),
         "f1": round(r["f1"], 4), "ungrounded_dropped": r["ungrounded"], "invalid_ids": r["invalid"],
         "parse_failures": r["failed"], "seconds_per_item": round(r["sec"], 2)} for r in rows]}
    write_json(EVAL_DIR / "results.json", results)
    print()
    for r in results["models"]:
        print(f"{r['model']:<14} precision {r['precision']:.2f}  recall {r['recall']:.2f}  f1 {r['f1']:.2f}  "
              f"{r['seconds_per_item']:.1f}s/item")
    print(f"\nSaved eval/results.json (public) and {EVAL_PRIVATE.relative_to(ROOT)} (private detail).")


# ---------------------------------------------------------------- bootstrap (saved results only)

def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def _item_counts(item: dict, area_of: dict, catalogue_key: dict) -> dict:
    gold, pred = set(item["gold"]), set(item["predicted"])
    missed = gold - pred
    rejected = {d["id"] for d in item.get("dropped_ungrounded", []) if d.get("id") in missed}
    as_new = {catalogue_key.get(_key(n.get("label", ""))) for n in item.get("new_skills", [])} & missed
    as_new -= rejected
    subs = [(w, [m for m in sorted(missed - rejected - as_new) if area_of.get(m) == area_of.get(w)])
            for w in sorted(pred - gold)]
    subs = [(w, s) for w, s in subs if s]
    tp, fp, fn = len(gold & pred), len(pred - gold), len(missed)
    bonus = len(rejected | as_new)
    return {"strict": (tp, fp, fn), "lenient": (tp + bonus, fp, fn - bonus),
            "rejected": sorted(rejected), "as_new": sorted(as_new), "subs": subs}


def _score(counts: list[dict], idx: list[int], mode: str) -> tuple[float, float, float]:
    return _prf(*(sum(counts[i][mode][k] for i in idx) for k in range(3)))


def _interval(values: list[float]) -> tuple[float, float]:
    v = sorted(values)
    return v[int(0.025 * (len(v) - 1))], v[int(round(0.975 * (len(v) - 1)))]


def run_bootstrap(skills_doc: dict, resamples: int, seed: int) -> None:
    if not EVAL_PRIVATE.exists():
        raise SystemExit("error: data/eval.private.yaml not found. Run --eval first.")
    ev = yaml.safe_load(EVAL_PRIVATE.read_text(encoding="utf-8"))
    skills = skills_doc["skills"]
    area_of = {s["id"]: s["area"] for s in skills}
    catalogue_key = {}
    for s in skills:
        catalogue_key[_key(s["id"])] = s["id"]
        catalogue_key[_key(s["label"])] = s["id"]
    models = list(ev["models"])
    item_ids = sorted(set.intersection(*(set(ev["models"][m]) for m in models)))
    if not item_ids:
        raise SystemExit("error: the models share no evaluated achievements")
    counts = {m: [_item_counts(ev["models"][m][i], area_of, catalogue_key) for i in item_ids] for m in models}

    rng = random.Random(seed)
    n = len(item_ids)
    samples = [[rng.randrange(n) for _ in range(n)] for _ in range(resamples)]  # shared across models (paired)
    full = list(range(n))
    boot = {m: {mode: [_score(counts[m], s, mode) for s in samples] for mode in ("strict", "lenient")} for m in models}
    source = {"run": ev.get("generated_at"), "eval_meta": ev.get("meta")}

    def ci(values: list[float]) -> list[float]:
        lo, hi = _interval(values)
        return [round(lo, 4), round(hi, 4)]

    boot_out = {"meta": {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "resampling_unit": "achievement", "n_achievements": n, "resamples": resamples,
                         "seed": seed, "interval": "percentile 95%", "paired": True, "source": source},
                "models": [], "pairwise_strict": []}
    for m in models:
        entry = {"model": m}
        for mode in ("strict", "lenient"):
            point = _score(counts[m], full, mode)
            entry[mode] = {name: {"value": round(point[k], 4), "ci95": ci([b[k] for b in boot[m][mode]])}
                           for k, name in enumerate(("precision", "recall", "f1"))}
        boot_out["models"].append(entry)
    for i, a in enumerate(models):
        for b in models[i + 1:]:
            pair = {"first": a, "second": b}
            for k, name in ((0, "precision_diff"), (2, "f1_diff")):
                d0 = _score(counts[a], full, "strict")[k] - _score(counts[b], full, "strict")[k]
                pair[name] = {"value": round(d0, 4),
                              "ci95": ci([x[k] - y[k] for x, y in zip(boot[a]["strict"], boot[b]["strict"])])}
            pair["share_first_higher_f1"] = round(
                sum(x[2] > y[2] for x, y in zip(boot[a]["strict"], boot[b]["strict"])) / resamples, 4)
            boot_out["pairwise_strict"].append(pair)
    write_json(EVAL_DIR / "bootstrap.json", boot_out)

    rows, private = [], {"source_run": ev.get("generated_at"), "models": {}}
    missed_all, wrong_all = Counter(), Counter()
    near_counts = []
    for m in models:
        near_counts.append({"model": m,
                            "grounding_rejected": sum(len(c["rejected"]) for c in counts[m]),
                            "listed_as_new": sum(len(c["as_new"]) for c in counts[m]),
                            "same_area_substitution": sum(len(c["subs"]) for c in counts[m])})
        private["models"][m] = {}
        for iid, c in zip(item_ids, counts[m]):
            item = ev["models"][m][iid]
            missed_all.update(set(item["gold"]) - set(item["predicted"]))
            wrong_all.update(set(item["predicted"]) - set(item["gold"]))
            rows += [[m, iid, s, "grounding_rejected", s] for s in c["rejected"]]
            rows += [[m, iid, s, "listed_as_new", ""] for s in c["as_new"]]
            rows += [[m, iid, g, "same_area_substitution", w] for w, gs in c["subs"] for g in gs]
            if c["rejected"] or c["as_new"] or c["subs"]:
                private["models"][m][iid] = {
                    "grounding_rejected": [d for d in item.get("dropped_ungrounded", []) if d.get("id") in c["rejected"]],
                    "listed_as_new": [x for x in item.get("new_skills", [])
                                      if catalogue_key.get(_key(x.get("label", ""))) in c["as_new"]],
                    "same_area_substitutions": [{"chosen": w, "instead_of": s} for w, s in c["subs"]]}
    with (EVAL_DIR / "near_misses.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["model", "achievement_id", "gold_skill", "category", "chosen_skill"])
        w.writerows(rows)
    write_json(EVAL_DIR / "error_patterns.json", {
        "meta": {"source": source, "n_achievements": n,
                 "near_miss_rules": {
                     "grounding_rejected": "model chose a hand-labelled skill, but its quote was not found verbatim in the input",
                     "listed_as_new": "model put a hand-labelled catalogue skill under new_skills (normalized id or label match)",
                     "same_area_substitution": "model chose a wrong skill from the same area as a missed hand-labelled skill; counted once per wrong proposal, while near_misses.csv has one row per (wrong, missed) pair",
                     "lenient_scoring": "grounding_rejected and listed_as_new count as hits; same_area_substitution does not"}},
        "near_miss_counts": near_counts,
        "missed_most_often": [{"skill": s, "count": k} for s, k in missed_all.most_common(10)],
        "wrongly_proposed_most_often": [{"skill": s, "count": k} for s, k in wrong_all.most_common(10)]})
    (DATA / "near_misses.private.yaml").write_text(
        "# PRIVATE: git-ignored. Near-miss cases with quoted evidence.\n"
        + yaml.safe_dump(private, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
    print(f"Wrote eval/bootstrap.json, eval/near_misses.csv, eval/error_patterns.json (public) and "
          f"data/near_misses.private.yaml (private) from {n} achievements x {len(models)} models.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", nargs="*", help="achievement ids to process (default: those with empty skills)")
    ap.add_argument("--model", default="gemma3:4b", help="model for proposals")
    ap.add_argument("--eval", action="store_true", help="evaluate against hand-labelled achievements")
    ap.add_argument("--models", nargs="+", default=["gemma3:4b", "qwen2.5:3b", "llama3.2:3b"], help="models for --eval")
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--dry-run", action="store_true", help="print the prompts for the first target and stop")
    ap.add_argument("--bootstrap-only", action="store_true",
                    help="skip extraction; compute bootstrap intervals from data/eval.private.yaml")
    ap.add_argument("--resamples", type=int, default=2000, help="bootstrap resamples (with --bootstrap-only)")
    ap.add_argument("--boot-seed", type=int, default=42, help="bootstrap seed (with --bootstrap-only)")
    args = ap.parse_args()

    if args.bootstrap_only:
        run_bootstrap(yaml.safe_load((DATA / "skills.yaml").read_text(encoding="utf-8")), args.resamples, args.boot_seed)
        return 0

    ach_path = DATA / "achievements.yaml"
    if not ach_path.exists():
        print("error: data/achievements.yaml not found", file=sys.stderr)
        return 1
    items = yaml.safe_load(ach_path.read_text(encoding="utf-8")).get("achievements", [])
    ex = Extractor(yaml.safe_load((DATA / "skills.yaml").read_text(encoding="utf-8")), items, args.host, args.timeout)

    if args.eval:
        run_eval(ex, items, args.models)
        return 0

    targets = [a for a in items if a["id"] in set(args.ids)] if args.ids else [a for a in items if not a.get("skills")]
    if not targets:
        print("Nothing to do: every achievement already has skills (use --ids to choose some).")
        return 0
    if args.dry_run:
        text = achievement_text(targets[0])
        print("=== AREA STEP ===\n" + ex.area_prompt() + "\n\n=== INPUT ===\n" + text)
        print("\n=== SKILL STEP (example: all areas) ===\n" + ex.skill_prompt([a["id"] for a in ex.areas])[0])
        return 0
    run_propose(ex, targets, args.model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
