---
name: literature-evidence
description: "Verify citations and establish what the literature supports: confirm each source exists and says what is claimed, compare papers on equal terms, check novelty and prior art, and record sources with separate identity, examination and support. Use when citing a paper, writing related work or a guide chapter, judging whether an idea is new, or checking a reference list."
---

# Literature evidence

Identity, examination and support are three different facts. Research OS records them separately (`research source`), offline.

1. **Record the source**: `research source add --doi 10.xxxx/… [--title …]` (or `--arxiv`, `--url`). Identity starts as *format-valid*, never *verified* by itself.
2. **Verify identity**: confirm title, authors, year and venue match the DOI/arXiv record from the publisher or an indexer you can reach (Crossref, OpenAlex, arXiv). Mark it only after you checked: `research source mark SRC-… --identity-verified`. If you cannot reach a service, leave it unverified and say so. Never invent or "complete" a reference from memory.
3. **Examine**: read the passage, figure or table you rely on and record where: `research source mark SRC-… --examined --locator "Fig. 3"`. An abstract is not examination.
4. **Assess support**: does it *support*, *partially support*, *contradict* or only give *context* for the sentence in your text? `research source mark SRC-… --finding F-… --verdict partially_supports --note "only the trend"`.
5. **Compare papers**: same task, data, metric, baseline and budget? Put a table of problem · method · data · metric · headline number · stated limits. Differences in setup are not differences in method.
6. **Novelty**: search for the closest prior work (several phrasings, the key equations, the application domain) and cite what is closest. Say "to our knowledge, within these searches" and list the searches. Do not claim novelty from absence of results in one search.
7. **Citations in the Guide**: put verified entries in `references.bib` (`research guide cite add`); cite only sources you examined. If a citation would help but is unverified, leave `<!-- cite: what is needed -->`.

## Red flags
A claim whose only source is a secondary summary · numbers copied without their conditions · a reference you cannot locate · a paper cited for a result it only motivates · retracted or superseded work cited as current.
