---
name: scientific-handoff
description: Convert an approved scientific discussion or researcher prompt into a Research OS Question, Plan, Experiment and Tasks without losing intent. Use when the human hands over a research objective.
---

# Scientific handoff

The researcher leads the science (possibly after discussing it elsewhere). You preserve that intent in existing entities;
do not add a new entity type and do not re-open the discussion.

## Procedure
1. `research search "…"` and `research current`: reuse an existing Question/Plan/Experiment if one covers this.
2. Extract, quoting the researcher's wording rather than paraphrasing: question and motivation; hypothesis and
   physical/mathematical assumptions; methodology; validation and evaluation design (including held-out data rules);
   success and failure criteria; known uncertainties; the limits of what you may decide.
3. List anything ambiguous or missing as **unresolved scientific decisions**. Ask the researcher; never pick silently.
4. Record:
   - `research question create "<question>" -d "<motivation, assumptions, uncertainties>"`
   - `research experiment create "<title>" -q Q-… --hypothesis "…" --success "…" --stop "…"` (parameters and evaluation design go in its description/parameters)
   - `research plan create "<title>" -o "<objective>" --success "<criteria>" -q Q-… --context "<delegated authority and boundaries>"`
   - `research task create PLAN-… "<title>" -g "…" --acceptance "…" -e EXP-… --check …` for each step.
5. Treat the approved specification and evaluation criteria as binding: no changing metrics, tolerances, splits or
   criteria, no tuning on held-out data, without the researcher's approval (record the request with `research task block`).
6. Mention that the Scientist Guide will need a refresh after the first synthesis (`research guide status`).
