# Skill extraction: evaluation results

This page evaluates the supplementary step in which a small local LLM suggests skill links for an achievement. The suggestions are a review aid; nothing reaches the knowledge graph until a person accepts it. The evaluation checks whether the pipeline produces useful suggestions on modest hardware. It is not a benchmark of the models.

## Summary

- **The three models cannot be separated on this data.** Every pairwise bootstrap interval includes zero. `qwen2.5:3b` has the highest precision (0.70), `gemma3:4b` the highest recall (0.43) and F1 (0.50), and `llama3.2:3b` trails on recall and F1.
- **About two out of three suggestions are correct; about half of the hand labels are found.** For a review aid, precision matters more than recall: a reviewer can add a missing skill, but every wrong suggestion has to be rejected.
- **The grounding check is not what limits recall.** Counting near-misses as hits raises F1 by only 0.01 to 0.04, so most misses are skills the models did not find.
- **Skills that are implied rather than stated are missed most** (PyTorch, clinician collaboration, reproducibility, data lineage). This supports the recommendation in the main README: use a 7B model for real use, and keep human review.
- **The run is reproducible.** Two separate runs (1 and 2 November 2026) gave identical precision, recall and F1 for every model.

## Setup

| | |
|---|---|
| Test set | 21 hand-labelled achievements with 75 hand-labelled skill links. The two few-shot examples (`ba-forecasting`, `sa-kpi`) are excluded. |
| Catalogue | 62 skills in 7 competency areas (`data/skills.yaml`), a closed vocabulary |
| Models | `gemma3:4b` (digest `68ceab7e468a`), `qwen2.5:3b` (`357c53fb659c`), `llama3.2:3b` (`a80c4f17acd5`), run locally with Ollama |
| Decoding | temperature 0, seed 42 |
| Pipeline | Two constrained calls per achievement. (1) The model picks 1 to 3 competency areas. (2) It picks skills only from those areas, via a JSON schema, and must quote the phrase that proves each skill. A skill whose quote is not found in the normalized input text is dropped. |
| Run | 2 November 2026, code at commit `b6bd560`, input hashes `df0d8b17f76c` (achievements) and `ca39c60943dd` (skills) |

**Metrics.** Scores are micro-averaged over all skill links. Precision is the share of suggested skills that match a hand label. Recall is the share of hand labels the model suggested. F1 is their harmonic mean.

## Results

Strict scores count only exact, grounded skill choices. Intervals are 95% percentile bootstrap intervals (see [Method](#method)).

| Model | Suggested | Correct | Precision | Recall | F1 | Sec / item |
|---|---|---|---|---|---|---|
| `gemma3:4b` | 53 | 32 | 0.60 [0.46, 0.74] | **0.43** [0.32, 0.54] | **0.50** [0.38, 0.62] | 6.7 |
| `qwen2.5:3b` | 40 | 28 | **0.70** [0.55, 0.86] | 0.37 [0.29, 0.46] | 0.49 [0.39, 0.57] | **3.6** |
| `llama3.2:3b` | 38 | 23 | 0.61 [0.43, 0.79] | 0.31 [0.21, 0.42] | 0.41 [0.28, 0.53] | 4.2 |

All models returned valid JSON for every item, and none proposed an id outside the catalogue. The grounding check dropped 1 (`gemma3:4b`), 4 (`qwen2.5:3b`) and 2 (`llama3.2:3b`) suggestions whose quote was not in the text.

### Model differences (strict, paired)

| Comparison | Precision difference | F1 difference | Resamples where the first has higher F1 |
|---|---|---|---|
| `gemma3:4b` − `qwen2.5:3b` | −0.10 [−0.28, +0.06] | +0.01 [−0.12, +0.13] | 57% |
| `gemma3:4b` − `llama3.2:3b` | −0.00 [−0.18, +0.16] | +0.09 [−0.05, +0.23] | 91% |
| `qwen2.5:3b` − `llama3.2:3b` | +0.09 [−0.04, +0.26] | +0.08 [−0.02, +0.20] | 93% |

No interval excludes zero. `gemma3:4b` and `qwen2.5:3b` are effectively tied on F1. Both score higher than `llama3.2:3b` in more than 90% of resamples, which suggests `llama3.2:3b` is weaker, but 21 achievements are not enough to confirm it.

### Strict and lenient scores

Lenient scores also count two kinds of near-miss as hits (defined below).

| Model | Strict F1 | Lenient F1 | Change |
|---|---|---|---|
| `gemma3:4b` | 0.50 [0.38, 0.62] | 0.52 [0.41, 0.63] | +0.02 |
| `qwen2.5:3b` | 0.49 [0.39, 0.57] | 0.50 [0.40, 0.59] | +0.01 |
| `llama3.2:3b` | 0.41 [0.28, 0.53] | 0.45 [0.32, 0.57] | +0.04 |

## Near-misses

A strict score treats a hand-labelled skill as missed whenever it is not among the accepted suggestions. Some of those misses are close to correct, so they are tracked separately.

### How they are found

For each model and achievement, let **G** be the hand-labelled skills, **P** the accepted suggestions, and **M = G − P** the missed skills. Each missed skill is checked against three rules, in this order:

| Category | Rule | Counted as a hit in lenient scores? |
|---|---|---|
| **Grounding rejected** | The model chose the skill, but its quote was not found in the normalized input text, so the grounding check dropped it, and the skill is in **M**. | Yes |
| **Listed as new** | The model put the skill under `new_skills` instead of choosing it from the catalogue. Matching normalizes the label (lowercase, every non-alphanumeric character as a space) and compares it with each catalogue id and label. | Yes |
| **Same-area substitution** | The model chose a wrong skill (in **P − G**) from the same competency area as a missed skill that is not already covered by the first two rules. | No, diagnostic only |

Same-area substitutions are not counted as hits because sharing an area does not make two skills interchangeable: `diagnostic-metrics` instead of `calibration` is close, while `regulated-pharma` instead of `iso-evidence` is not. They show where the model found the right area but picked the wrong skill within it. Each wrong suggestion is counted once; `near_misses.csv` lists every (wrong, missed) pair, so it has more substitution rows than the counts below.

### Counts

| Model | Grounding rejected | Listed as new | Same-area substitutions |
|---|---|---|---|
| `gemma3:4b` | 1 | 1 | 11 |
| `qwen2.5:3b` | 1 | 0 | 5 |
| `llama3.2:3b` | 1 | 2 | 7 |

What the cases show:

- **One grounding rejection is systematic.** All three models chose `zero-few-shot-benchmarking` for `th-benchmark`, and all three were rejected. The facts list "zero-shot" and "few-shot" as separate YAML items, so no single quote can match the text verbatim. This is an input-format issue, not a model error.
- **Catalogue skills listed as new:** `gemma3:4b` proposed "ECG" as a new skill, and `llama3.2:3b` did the same with "code-review" and "clustering". All three are already in the catalogue.
- **`gemma3:4b` has the most substitutions.** It usually picks the right area but a neighbouring skill, for example `diagnostic-metrics` instead of `calibration` and `cross-validation` in `kr-models`.

## Error patterns

Counted over all three models.

| Missed most often | Times | | Wrongly suggested most often | Times |
|---|---|---|---|---|
| `pytorch` | 10 | | `code-review` | 4 |
| `clinician-collaboration` | 7 | | `data-pipelines` | 4 |
| `data-validation` | 6 | | `python` | 4 |
| `data-lineage` | 6 | | `performance-optimization` | 4 |
| `sql-relational` | 6 | | `diagnostic-metrics` | 3 |
| `stakeholder-reporting` | 6 | | `feature-importance` | 3 |
| `reproducibility` | 6 | | `ci-azure-devops` | 3 |
| `diagnostic-metrics` | 6 | | `logistic-regression` | 2 |
| `ecg` | 5 | | `regulated-pharma` | 2 |
| `iso-evidence` | 4 | | `zero-few-shot-benchmarking` | 2 |

- **Missed skills are mostly implied, not stated.** A reader infers clinician collaboration or reproducibility from context, but the text rarely names them. The grounding rule requires a verbatim quote, which makes these skills hard to suggest by design. Part of the gap therefore comes from how the hand labels were written, not only from the models.
- **Wrong suggestions are mostly generic skills** that fit almost any technical text (`python`, `data-pipelines`, `code-review`). Small models drift toward them when the specific skill is unclear.

## Interpretation

- **The pipeline works as a review aid.** Output is always valid JSON, always within the catalogue, and about 60 to 70% of suggestions are correct. A reviewer accepts most suggestions and adds the implied skills by hand.
- **Among the ≤4B models, there is no clear winner.** `qwen2.5:3b` gives fewer, more precise suggestions and is fastest; `gemma3:4b` finds more but makes more mistakes.
- **For real use, a larger model is the better choice.** The main weakness, implied skills, is a reasoning gap that larger models are more likely to close. A 7B model was not part of this evaluation, so the recommendation is a reasoned expectation, not a measured result.

## Limitations

- **Small test set.** 21 achievements and 75 skill links give wide intervals. Differences below about 0.15 cannot be resolved.
- **One annotator.** The hand labels are the author's own and include implied skills. A second annotator would show how consistent the labels themselves are.
- **Same-area substitutions use a coarse rule.** Sharing an area does not guarantee semantic similarity, which is why they are excluded from lenient scores.
- **Code state.** The run metadata shows the scripts had uncommitted changes relative to commit `b6bd560` at run time. The metrics match an earlier run exactly, but strict reproduction requires re-running from a clean commit.

## Method

**Bootstrap.** Achievements are resampled with replacement (2000 resamples, seed 42), and precision, recall and F1 are recomputed on each resample. Intervals are the 2.5th and 97.5th percentiles. Every model uses the same resamples, so model differences are paired: each resample compares the models on the same achievements.

**Reproduce.**

```bash
python scripts/extract_skills.py --eval --models gemma3:4b qwen2.5:3b llama3.2:3b
python scripts/extract_skills.py --bootstrap-only
```

The first command needs the private `data/achievements.yaml`. The second reads only the saved results and calls no model.

## Files

| File | Contents |
|---|---|
| [`results.json`](results.json) | Strict metrics per model and run metadata (model digests, git commit, input hashes, decoding settings) |
| [`bootstrap.json`](bootstrap.json) | Strict and lenient intervals, paired model differences |
| [`near_misses.csv`](near_misses.csv) | One row per near-miss: model, achievement, hand-labelled skill, category, chosen skill |
| [`error_patterns.json`](error_patterns.json) | Near-miss rules and counts, most-missed and most wrongly suggested skills |

Per-item predictions and the quoted evidence stay in git-ignored files. Public files contain skill and achievement ids only.
