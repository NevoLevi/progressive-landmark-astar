# Progressive-landmarks course paper

The public artifact intentionally omits `aaai2027.sty`. Download the official
AAAI'27 author kit from the URL in `AAAI27_AUTHOR_KIT_PROVENANCE.md`, verify the
recorded archive and file SHA-256 values, and copy the unmodified style beside
`main.tex` before compiling. The included `aaai2027.bst` states its LPPL terms
in its header.

`main.tex` is the final AAAI'27-style source for **Progressive Landmark
Evaluation in A*: An Empirical Study of Nested Differential Heuristics**.  It
uses the unmodified official `aaai2027.sty` and `aaai2027.bst` assets recorded
in `AAAI27_AUTHOR_KIT_PROVENANCE.md`, camera-ready formatting, and the
course-recommended `\nocopyright` setting.

The paper uses only these scientific artifacts:

- `../data/results/progressive_landmarks_v2_rerun1/`
- `../data/processed/progressive_landmarks_analysis_v2/`

The five PDFs and generated hypothesis table copied into `generated/` are
byte-identical to the analysis outputs; their source hashes are listed in
`generated/README.md`.  The compact method, map, and preprocessing tables were
made from immutable `summary.json` and `map_metrics.csv` values.

## Administrative blockers

Before submission, replace both explicit author/ID markers in `main.tex` and
the public repository URL/commit-SHA blocker in
`sections/10_reproducibility.tex`.  No guessed identity or repository URL is
present.

The checked-in `main.pdf` is an eight-page, warning-clean official-style draft
compiled with pdfTeX and visually inspected page by page. It deliberately
retains those three administrative placeholders and must be rebuilt and
rechecked after they are filled.

## Build

Run from this directory:

```powershell
pdflatex -interaction=nonstopmode -halt-on-error main.tex
bibtex main
pdflatex -interaction=nonstopmode -halt-on-error main.tex
pdflatex -interaction=nonstopmode -halt-on-error main.tex
```

Or use:

```powershell
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
```

The bibliography is `../references/references.bib`.  A final release audit
must show no unresolved references/citations, overfull boxes, clipped or
illegible figures, or superseded project text.  Render every PDF page and visually
inspect the two-column layout after the author and repository blockers are
filled.
