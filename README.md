# Competency Graph

An evidence-based knowledge graph of my skills: every skill links to the achievements that demonstrate it, and every achievement to the role or project it came from.

The source data stays private. A build script validates it and publishes only labels and links.

## How it works

```
data/achievements.yaml        private, git-ignored: facts, skills and wordings per achievement
data/skills.yaml              public: skill catalogue (label, area, level, ESCO link)
data/corrections.private.yaml private, git-ignored: claims the build refuses to publish
        │
        │  python scripts/build_graph.py
        ▼
site/graph.json               public: nodes and links only (fields listed in PUBLIC_FIELDS)
site/index.html               public: D3 page that draws graph.json
```

The build stops if:
- an achievement references an unknown role, project or skill,
- a required field or valid status is missing,
- a label or wording matches a blocked pattern from the private corrections file.

Achievements marked `needs-check` are left out unless you pass `--include-needs-check`. Each published achievement carries a `source_hash` of its private record, so changes are traceable without exposing the content.

## Run it

```bash
pip install -r requirements.txt
python scripts/build_graph.py            # real data (private file required)
python scripts/build_graph.py --example  # example data, works for anyone
python -m http.server -d site            # then open http://localhost:8000
```

## Layout

```
data/
  achievements.example.yaml   made-up entries showing the schema
  skills.yaml                 skill catalogue
scripts/
  build_graph.py              validate, apply publication policy, write site/graph.json
site/
  index.html                  the graph page
  graph.json                  generated
```

## Next

- Map skills to ESCO concepts (`esco:` field in skills.yaml).
- `scripts/extract_skills.py`: an LLM with structured outputs proposes skills for achievements with an empty `skills` list, for review.
- GitHub Pages deployment of `site/`.

## Rules

- One record per achievement. Facts are written once; wordings live under `phrasings`.
- Every skill needs evidence, or is marked `familiar`.
- Never commit application history, cover letters, contact details, employer-internal material, or clinical data.
