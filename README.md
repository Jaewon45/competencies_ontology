# Competency Graph

A knowledge graph of my skills, built from evidence: every skill links to the roles and projects that prove it.

## Status

Draft data only. Next steps: skill taxonomy, extraction, build and visualization.

## Layout

```
data/
  achievements.yaml   # canonical achievements: facts, skills, phrasings (single source of truth)
  skills.yaml         # (next) skill ids, labels, levels (proven / familiar), aliases, ESCO links
  publications.bib    # (next) BibTeX for published and submitted papers
  coursework.yaml     # (next) course names only, no grades
  corrections.private.yaml  # git-ignored: false claims the build must block
  taxonomy/           # (next) ESCO subset used for normalization, with version and download date
scripts/
  extract_skills.py   # (next) LLM with structured outputs proposes skills for achievements with empty `skills`
  build_graph.py      # (next) validates data, builds graph.json with lineage per node
  check_claims.py     # (next) fails the build if a claim listed under `corrections` reappears
site/
  index.html          # (next) D3 graph, published with GitHub Pages
```

## Rules

- One record per achievement. Wordings live under `phrasings`; facts are written once.
- Every skill needs evidence, or is marked `familiar`.
- Phrasings are labelled by emphasis (`default`, `short`, `rigor`), never by target company.
- Never commit application history, cover letters, contact details, employer-internal material, or clinical data (eICU is credentialed PhysioNet data).
