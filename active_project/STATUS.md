# Active Project Status

Updated: 2026-08-12

## Current decision

The active project is **Progressive Landmark Evaluation in A***. It compares
Manhattan, eager 32-landmark, ordinary Lazy 32-landmark, and a fixed staged
`Manhattan -> 4 landmarks -> 32 landmarks` schedule in optimal single-agent
four-neighbor grid search.

This is the intentionally reduced-scope replacement for the earlier MVC/CBS
program. The previous work is frozen, not deleted, and is outside this
project's evidence chain.

## Evidence state

| Item | State | Direct evidence / implication |
|---|---|---|
| Instructions, examples, and collision screen | Complete for the current local snapshot | The topic satisfies the required practical search study; recheck the chosen-project register before submission. |
| Proposal-level literature boundary | Complete | Primary sources establish A*, ALT/DH, landmark subsets, Lazy/Rational Lazy A*, Selective Max, and dynamic-heuristic context; the report citation audit is complete. |
| Frozen protocol v2 | Complete and verified | [`configs/progressive_landmarks_v2.json`](../configs/progressive_landmarks_v2.json) fixes `K=4`, `M=32`, 12 maps, 960 queries, balanced rotations, and 34,560 searches. |
| Core, runner, external gate, and analysis | Complete and tested | Strict replay, tamper, matrix, path, BFS, table, counter, and trace-digest gates are implemented under [`src/python/progressive_landmarks`](../src/python/progressive_landmarks/). |
| Formal development | Complete | 160 queries, four maps, and 5,760 searches passed the external replay gate with no tuning or selection. |
| Sealed evaluation | Complete | 800 queries, eight maps, and 28,800 searches passed validation; all planned queries were retained. |
| Analysis and figures | Complete | 800 query rows, eight map rows, three hypothesis rows, and ten PNG/PDF figures are bound by the analysis manifest. |
| Submission report | Complete draft | Narrative integration, citation audit, official PDF build, and rendered-page QA passed; only the explicit identity/code-link placeholders remain. |
| Public release boundary | Complete draft; isolated validation passed | A deterministic 152-file allowlist excludes private course files, superseded attempts, MVC/CBS history, and the non-redistributable `aaai2027.sty`. The current workspace eight-file active-public lane is `140 passed, 2 skipped`; the draft archive passed isolated validation, while final non-draft packaging remains gated on administrative metadata. |
| Identities and repository hosting | Pending | Student names/IDs and an immutable public repository URL/commit are not yet supplied. |

The pre-documentation full working-archive checkpoint was `365 passed,
1 skipped`, including historical MVC/CBS modules. The current workspace
eight-file active-public lane is `140 passed, 2 skipped`; both skips are
optional Windows symlink-privilege branches in the runner safe-child and
metadata-finalizer tests. All scientific gates passed. Two builds of the
finalized 152-file draft boundary produced byte-identical ZIPs; the generated
manifest binds every member and its own canonical self-hash without a circular
archive-hash claim inside the archive.
A fresh extraction passed the exact-plan protocol verifier, syntax compilation,
the self-contained analysis loader, and an active audit with `PASS=8`,
`PENDING=2` (administrative only), `FAIL=0`, and `WARN=0`. Its full public suite reported
`139 passed, 3 skipped`: the same two privilege skips plus the
`test_repro_audit` historical-checkpoint case intentionally absent from the
public artifact. It also confirmed `aaai2027.sty` is absent; AAAI prohibits its
redistribution without written permission, and the pinned official-kit URL and
hashes needed to retrieve it are preserved in report provenance.

## Authoritative evidence

Only these corrected paths may support project claims:

- development: `data/results/progressive_landmarks_v2_rerun1/development/`;
- audit: `data/results/progressive_landmarks_v2_rerun1/development_audit.json`;
- evaluation authorization:
  `data/results/progressive_landmarks_v2_rerun1/sealed_evaluation_freeze.json`;
- sealed evaluation:
  `data/results/progressive_landmarks_v2_rerun1/sealed_evaluation/`; and
- processed analysis: `data/processed/progressive_landmarks_analysis_v2/`.

Key completion-marker SHA-256 values are, respectively,
`1d993d4ac7106730f5cddbfe7b5c15d979e8e4da9652b2a2d9f67b781d68814d`,
`d8792f0d34ef344b9dcd7aa441b4463c38769724803f2b9e807ba992fa8beab1`,
`3bce31ce4f942eccb0a0fc18c302e47fa477503a06b466cf6d798b21340f0e72`,
`edaea56bb3aaa0b55b903e6dcde9692217a9d24a77da6a66bb52c1e583e62d53`,
and `47c3244fedcd52d2da0fa6f4889e0cb0cdb3289306f8b0ca69792149096c66df`.
See [`EXPERIMENT_PROVENANCE.md`](EXPERIMENT_PROVENANCE.md) for every raw and
processed binding.

## Superseded attempt

The old top-level v2 development/audit/freeze/evaluation directories are
retained as an audit trail but are not authoritative. Their analysis failed
closed before statistical output because an immutable tuple representation was
compared with a JSON-list representation. A single representation-normalizing
analysis fix was made; protocol, data, methods, search code, hypotheses, and
estimands were unchanged. The entire bound matrix was mechanically rerun under
a new aggregate code hash, and no tuning or post-hoc exclusion occurred.

## Results headline

- H1: 800/800 BFS-cost and full-landmark trace-invariance gates passed.
- H2: `staged` saved median 3.0976% pivot evaluations and 3.0172% physical
  table reads relative to `lazy_full`.
- H3: the paired `staged/lazy_full` search-time ratio was 1.0543, with
  hierarchical-bootstrap 95% interval `[0.9890, 1.1016]`. This does not
  establish an overall speedup; the result was topology-dependent.
- RQ4 diagnostic: landmark preprocessing was unattractive for one-off search
  and became competitive only under high same-map query reuse in the
  predeclared model. This was not a fourth registered hypothesis.

The maze family favored staging in median search time; random, room, and
warehouse did not. The exact saved-work mechanism is supported, while a
universal runtime advantage is not.

## Frozen production contract

- Domain: `.` cells in undirected unit-cost four-neighbor Moving AI grids.
- Development: four source-`train` maps and 160 queries.
- Sealed evaluation: four source-`validation` and four source-`holdout` maps,
  800 queries, with no development-map overlap.
- Landmarks: deterministic row-major-first then farthest-first placement,
  `K=4`, `M=32`, and no candidate set or tuning.
- Methods: `manhattan`, `eager_full`, `lazy_full`, and `staged`.
- Timing: one warm-up plus eight left-rotated timed repetitions; every method
  occupies each position exactly twice per query.
- Primary timing unit: the ordinary even median of eight per query/method;
  repetitions are not independent samples.
- Statistical interval: 10,000 map-then-query hierarchical bootstrap
  replicates with seed `23725513`.
- Negative, mixed, and topology-dependent results are retained.

## Governing active documents

- [`TOPIC_PROPOSAL.md`](TOPIC_PROPOSAL.md): motivation, RQs, novelty boundary,
  and frozen scope.
- [`PROJECT_SPEC.md`](PROJECT_SPEC.md): exact algorithms, protocol, metrics,
  analysis, threats, and acceptance gates.
- [`LITERATURE_MAP.md`](LITERATURE_MAP.md): primary-source claim map and final
  citation tasks.
- [`EXPERIMENT_PROVENANCE.md`](EXPERIMENT_PROVENANCE.md): authoritative hashes,
  rerun history, environment, and result ledger.
- [`PLAN.md`](PLAN.md): remaining submission work.
- [`STATUS.md`](STATUS.md): this evidence ledger.

Root-level MVC/CBS documents are historical side material.

## Remaining actions

1. Stage exactly the 152-file allowlist as scientific commit A, configure its
   canonical public HTTPS remote, push A, and independently verify its commit
   URL is publicly readable.
2. Require the metadata-finalizer inputs to equal local `HEAD` (commit A) and
   the configured remote; run `--dry-run` before `--apply` with the supplied
   student identities.
3. Rebuild and visually inspect the PDF, rerun the isolated and complete
   audits, and create metadata/report/status-only commit B without changing
   frozen scientific evidence.

## Completion assessment

Topic selection, protocol, implementation, development, sealed evaluation,
replay validation, statistical analysis, tables, and figures are complete.
The overall course-project goal remains active only because identity fields and
immutable public repository hosting are pending.
