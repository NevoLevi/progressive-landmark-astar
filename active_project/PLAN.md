# Execution Plan: Progressive Landmark Evaluation in A*

Updated: 2026-08-12

Current estimate: **only administrative metadata remains**. The scientific
implementation, formal runs, replay audit, analysis, tables, report, official
PDF build, and rendered-page inspection are complete.

## Operating rule

The scientific evidence is frozen. Do not change `K=4`, `M=32`, maps, query
selection, methods, timing rotations, hypotheses, estimands, bootstrap design,
or exclusions. Do not regenerate or overwrite the authoritative raw/processed
directories. Any report correction must trace to the generated analysis, not
to hand-edited numbers.

Earlier MVC/CBS work and disposable feasibility probes remain frozen side
material and must not enter the active report's evidence chain.

## Completed milestones

### M0 - Course, novelty, and collision boundary: complete

- current instructions and supplied examples audited;
- chosen-project register screened for direct collision;
- proposal-level primary-source boundary established;
- contribution limited to a controlled empirical intersection rather than an
  algorithm-invention claim.

### M1 - Frozen protocol and tested core: complete

- strict checksum-bound v2 plan with 12 maps, 960 queries, and 34,560 searches;
- deterministic BFS, landmark tables, four A* modes, stable OPEN ordering,
  caching, counters, and trace digests;
- parser, property, tamper, path, start/goal, and exhaustive correctness tests.

### M2 - Production runner and formal development: complete

- immutable manifest-last runner and external fail-closed replay gate;
- 160 development queries, four maps, and 5,760 searches;
- exact plan/schedule/method/counter/path/BFS/table/digest reconciliation;
- `selection_performed=false`; no `K` or method tuning.

Gate G2 passed. The authoritative development manifest is
`data/results/progressive_landmarks_v2_rerun1/development/manifest.json`, SHA-256
`1d993d4ac7106730f5cddbfe7b5c15d979e8e4da9652b2a2d9f67b781d68814d`.

### M3 - Locked sealed evaluation: complete

- external development audit and freeze issued before evaluation;
- 800 queries, eight maps, and 28,800 searches under unchanged v2;
- zero missing queries, post-hoc exclusions, BFS mismatches, or unexplained
  full-landmark digest mismatches;
- all raw observations, manifests, code/source/environment bindings preserved.

Gate G3 passed. The authoritative evaluation manifest is
`data/results/progressive_landmarks_v2_rerun1/sealed_evaluation/manifest.json`,
SHA-256
`edaea56bb3aaa0b55b903e6dcde9692217a9d24a77da6a66bb52c1e583e62d53`.

### M4 - Analysis and figures: complete

- correctness/invariance gates replayed before performance access;
- paired per-query work savings, OPEN work, suffix avoidance, and timing ratios;
- per-map, family, and overall summaries;
- 10,000-replicate fixed-seed hierarchical bootstrap;
- preprocessing amortization for `Q in {1,10,100,1000}`;
- three hypothesis rows and five figures in PNG and PDF.

Gate G4 passed. The authoritative analysis manifest is
`data/processed/progressive_landmarks_analysis_v2/manifest.json`, SHA-256
`47c3244fedcd52d2da0fa6f4889e0cb0cdb3289306f8b0ca69792149096c66df`.

The result is deliberately mixed: staging saves about 3% of exact landmark
work but has paired runtime ratio 1.0543 with a 95% interval crossing 1. The
report must emphasize the mechanism and topology-dependent crossover, not
claim a universal speedup.

## Documented mechanical rerun

The first top-level v2 raw bundle is superseded. Its analysis stopped before
statistical output on a tuple/list representation mismatch. One normalization
fix changed only `analysis.py`; the protocol, runner, core, data, methods,
hypotheses, and estimands remained fixed. Because raw manifests bind the full
code aggregate, both splits were rerun and independently reauthorized. See
[`EXPERIMENT_PROVENANCE.md`](EXPERIMENT_PROVENANCE.md). No tuning occurred.

## Remaining milestones

### M5 - Submission report: complete except administrative metadata

Target: a concise 8-12 page report in the selected academic format.

Required work:

1. Replace the old MVC/CBS report narrative with the active project.
2. Integrate generated tables and figures directly from the authoritative
   analysis directory.
3. Answer RQ1-RQ4 with the locked negative/mixed result.
4. Verify every literature claim at page/section level and retain the honest
   novelty wording from `LITERATURE_MAP.md`.
5. Explain exact counters versus noisy timing, map-family heterogeneity,
   preprocessing amortization, and all threats to validity.
6. Add the immutable code/reproduction link once hosting is available.
7. Build the PDF and inspect every page for clipping, illegible labels, broken
   citations, missing references, and placeholder leakage.

The narrative, generated tables/figures, citations, official PDF build,
identities, immutable scientific-snapshot link, and rendered-page checks are
complete. Gate G5 passes.

### M6 - Final audit and handoff: complete

Required work:

- Protocol verification, compileall, the chosen-project register check,
  prior-art boundary audit, and draft-PDF visual inspection are complete. The
  current workspace eight-file active-public lane is `140 passed, 2 skipped`;
  both skips are optional Windows symlink-privilege branches (runner safe-child
  and metadata finalizer).
- The standard reproducibility audit passes with no pending items, warnings, or
  failures under `--require-complete`.
- Two deterministic builds of the pre-metadata 152-file draft boundary produced
  byte-identical ZIPs. The generated manifest binds every member and its own
  canonical self-hash; no circular archive hash is embedded inside the archive.
  A fresh extraction reported `139 passed, 3 skipped` across the full public
  suite: the same two privilege skips plus the `test_repro_audit` historical
  checkpoint intentionally absent from the public artifact. Its protocol
  verifier reproduced the exact plan SHA, compileall and the self-contained
  analysis loader passed, and the active audit reported `PASS=8`, `PENDING=2`
  (administrative only), `FAIL=0`, and `WARN=0`. The extraction confirmed that
  `aaai2027.sty` is absent because AAAI prohibits redistribution without
  written permission; its pinned official-kit retrieval URL and hashes remain
  in report provenance.
- Scientific commit `4ee61db6787528efb7e01326e3c23d0006515570` is public and
  independently readable over unauthenticated HTTPS.
- The Git-bound metadata finalizer passed `--dry-run` before `--apply` with the
  supplied names, IDs, repository URL, and exact scientific SHA.
- The eight-page final PDF passes marker, text, log, bibliography, and rendered
  page inspection; final non-draft release validation passes.
- The final publication commit is limited to identity metadata, report layout
  and PDF, and administrative status/provenance. Frozen scientific evidence is
  unchanged from the public scientific snapshot.

Gate G6 passes only when report, code, inputs, configuration, raw results,
analysis, figures, citations, identities, and reproduction instructions agree.

## Deliverable layout

```text
active_project/
  TOPIC_PROPOSAL.md
  PROJECT_SPEC.md
  LITERATURE_MAP.md
  EXPERIMENT_PROVENANCE.md
  STATUS.md
  PLAN.md
configs/progressive_landmarks_v2.json
src/python/progressive_landmarks/
scripts/*progressive_landmarks*.py
tests/python/test_progressive_*.py
data/results/progressive_landmarks_v2_rerun1/
data/processed/progressive_landmarks_analysis_v2/
report/
```

## Scope-based stop line

The project is complete when G6 passes, not when every landmark variant has
been explored. Alternative placement, adaptive prefixes, RLA*, other motion
models, native-code optimization, and additional domains are future work.
