# Research website example

A synthetic diffusion experiment that produces a figure and a Quarto report, and how it becomes a page on your project's website.
`gen_diffusion.py` is standard-library Python: it writes `reports/quarto/chapters/00-smoke.qmd`, `reports/quarto/figures/diffusion.png`
and a small CSV table, which is the layout `research site add` reads.

```bash
research init demo --layout ledger --name "Diffusion study" --goal "How does a spike spread?" --agents claude --apply
cd demo/main
cp ../../examples/research-website/gen_diffusion.py . && git add gen_diffusion.py && git commit -m "diffusion generator"

# 1. the website exists already (new projects get it); look at it locally, no GitHub needed
research site status
research site preview            # http://localhost:4200/   (over SSH: ssh -L 4200:localhost:4200 <user>@<host>)

# 2. run the experiment through the supervisor so it is verified, then publish it
research plan yaml template -o plans/research.yaml      # a task whose command is: python3 gen_diffusion.py
research plan yaml apply plans/research.yaml && research plan run PLAN-001
research site add EXP-001 --dry-run                     # what would be published, what is excluded and why
research site add EXP-001                               # draft page + report chapter + figure
research site build                                     # renders, checks links and figures

# 3. edit docs/scientist-guide/experiments/<slug>/index.qmd (fill in the TODOs), then, when YOU decide:
git add docs/scientist-guide .github && git commit -m "Research website" && git push origin main
#    GitHub: Settings -> Pages -> Source: GitHub Actions (once). The workflow deploys what is committed.

# Unpublish without deleting anything (previews first; add --apply to do it)
research site remove EXP-001
research site remove EXP-001 --apply                    # moved to docs/scientist-guide/_unpublished/, restorable with `site add EXP-001`
```

The test `tests/python/test_site_e2e.py` runs this flow end to end, including the local build and the check that the generated workflow deploys the same output.
