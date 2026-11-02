# Competency Graph

**[Open the live graph →](https://jaewon45.github.io/competencies_ontology/)**

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
docs/graph.json               public: nodes and links only (fields listed in PUBLIC_FIELDS)
docs/index.html               public: D3 page that draws graph.json
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
python -m http.server -d docs            # then open http://localhost:8000
```

### Propose skills with a local LLM (supplementary)

`scripts/extract_skills.py` suggests skill links for new or unlabelled achievements, using small open-weight models (≤4B) running locally with [Ollama](https://ollama.com), so private text never leaves the machine. Suggestions are a review aid only: nothing reaches `achievements.yaml` or the graph until a person accepts it.

Each proposal takes two constrained calls. The model first picks 1 to 3 competency areas, then picks skills only from those areas (JSON schema, closed vocabulary) and must quote the phrase that proves each one. Skills whose quote is not found in the input are dropped.

```bash
ollama pull gemma3:4b
python scripts/extract_skills.py --model gemma3:4b            # achievements with an empty skills list
python scripts/extract_skills.py --model gemma3:4b --dry-run  # show the prompts only
```

For real use, a 7B model (for example `qwen2.5:7b`, via `--model`) is recommended. The ≤4B evaluation below is a sanity check of the pipeline, not a model benchmark.

### Evaluate the suggestions

`--eval` runs every model on the hand-labelled achievements (few-shot examples excluded) and compares the suggestions with the hand labels. `--bootstrap-only` then reuses the saved results, without extraction or any model call, to add confidence intervals and near-miss analysis. Results and their interpretation are in [`eval/RESULTS.md`](eval/RESULTS.md).

```bash
ollama pull qwen2.5:3b && ollama pull llama3.2:3b
python scripts/extract_skills.py --eval --models gemma3:4b qwen2.5:3b llama3.2:3b
python scripts/extract_skills.py --bootstrap-only
```

## Layout

```
data/
  achievements.example.yaml   made-up entries showing the schema
  skills.yaml                 skill catalogue
scripts/
  build_graph.py              validate, apply publication policy, write docs/graph.json
  extract_skills.py           suggest skills with a local LLM; --eval compares models, --bootstrap-only adds intervals
eval/
  RESULTS.md                  evaluation write-up (written by hand)
  results.json                metrics per model and run metadata (model digests, git commit, input hashes)
  bootstrap.json              95% intervals and paired model differences
  near_misses.csv             one row per near-miss case (ids only)
  error_patterns.json         near-miss rules and counts, most-missed and most wrongly proposed skills
docs/
  index.html                  the graph page
  graph.json                  generated
```

## Next

- Map skills to ESCO concepts (`esco:` field in skills.yaml).

## Rules

- One record per achievement. Facts are written once; wordings live under `phrasings`.
- Every skill needs evidence, or is marked `familiar`.
- Never commit application history, cover letters, contact details, employer-internal material, or clinical data.
