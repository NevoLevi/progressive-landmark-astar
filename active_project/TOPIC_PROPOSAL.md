# Topic Proposal: Progressive Landmark Evaluation in A*

Status: selected direction; protocol, implementation, formal evaluation, and analysis complete, 2026-08-12

## One-sentence proposal

Study whether a cheap, fixed prefix of differential-landmark lookups can act as an effective intermediate filter inside Lazy A*, reducing the number of remaining landmark evaluations enough to offset one additional OPEN-list cycle while preserving optimal four-neighbor grid paths.

## Why this is the right-sized course project

The authoritative project instructions, distributed through the course site,
require practical research on a course topic, a motivated modification or
evaluation of an existing search method, a comprehensive literature review,
reproducible methodology, experiments with tables and figures, conclusions and
limitations, and a code link. They explicitly permit a testable extension to
an existing evaluation, expansion, or search mechanism.

This proposal stays close to A*, admissible and consistent heuristics, OPEN ordering, heuristic dominance, and Lazy A*. Its scale is deliberately comparable to the supplied prior-year examples:

- The supplied prior-year report *Heuristic Stacking in Sliding Tile Puzzles*
  asks whether a small extension of Lazy A* is useful and accepts a mixed or
  negative result.
- The supplied prior-year report *Early vs Late A Comparison* studies one
  subtle A* design choice using mechanism counts as well as runtime.
- The accompanying course note says the examples predate the current
  instructions, so they calibrate scope but do not replace this year's
  requirements. These course-distributed files are intentionally not
  redistributed in the public artifact.

## Research object

For an undirected unit-cost grid, an ordered landmark set `L=(l_1,...,l_M)`, and goal `t`, define

```text
phi_i(v,t) = |d(l_i,v) - d(l_i,t)|
H_k(v,t)   = max(Manhattan(v,t), phi_1(v,t), ..., phi_k(v,t)).
```

The prefixes are fixed and nested, so `H_0 <= H_K <= H_M` pointwise. Both sizes were fixed prospectively: `K=4` and `M=32`. There is no development-set tuning of `K`.

The proposed method uses the fixed sequence

```text
Manhattan -> first 4 landmarks -> remaining 28 landmarks.
```

A non-goal state is reinserted into OPEN after each required refinement and is expanded only after reaching `H_32`. Already computed prefix values are reused. Following standard Lazy-A* initialization, every landmark method fully evaluates the start before its first insertion; `staged` does the prefix and suffix work without requeuing the start. A valid popped goal terminates before expensive refinement because every admissible stage is zero at the goal.

## Four frozen methods

1. `manhattan`: ordinary A* using `H_0`.
2. `eager_full`: ordinary A* evaluating `H_32` when a state is accepted.
3. `lazy_full`: ordinary two-stage Lazy A*, `H_0 -> H_32`, for accepted non-start states.
4. `staged`: the proposed three-stage schedule, `H_0 -> H_4 -> H_32`, for accepted non-start states.

All methods use the same graph semantics, duplicate handling, valid-pop goal test, and OPEN order `(f, -g, ordinal, row-major index, version, stage)`. A lazy promotion preserves the state's ordinal; a successful better-`g` update receives a fresh ordinal. Heuristic stage values are cached by state and reused after a better-`g` update. The three full-landmark methods should have identical optimal costs and successor-enumeration traces; they differ in landmark work and OPEN work.

## Research questions

- **RQ1 - Mechanism:** How many remainder-stage pivot evaluations and actual distance-table reads does the intermediate prefix avoid relative to `lazy_full`?
- **RQ2 - Net cost:** When do those savings outweigh the extra heap pop/push cycle introduced by the intermediate stage?
- **RQ3 - Topology:** Does that balance differ across maze, random, room, and warehouse maps?
- **RQ4 - Amortization:** After landmark preprocessing is amortized over repeated queries on a map, when do landmark methods beat Manhattan A* end to end?

## Pre-registered hypotheses

- **H1 (correctness/invariance):** Every method returns the independent four-neighbor BFS cost. Under the frozen tie rule, `eager_full`, `lazy_full`, and `staged` have identical successor-enumeration traces.
- **H2 (work saving):** `staged` uses fewer pivot evaluations and distance-table reads than `lazy_full` on the median sealed-evaluation query because some prefix-refined states never reach the suffix stage.
- **H3 (conditional runtime):** `staged` is most likely to help where `H_4` often raises a node's key enough to delay it past the goal. It can lose where the prefix rarely filters a node because it pays an additional OPEN cycle.
- **RQ4 expectation (preprocessing diagnostic):** Landmark preprocessing is unattractive for a single query but can become competitive when many queries share a map. No universal crossover is assumed. This sensitivity calculation is descriptive, not a fourth registered pass/fail hypothesis.

Hypotheses may be rejected. A well-explained negative or topology-dependent result is valid.

## Honest novelty boundary

This is a course-level empirical contribution, not a claim to have invented landmark heuristics or multi-stage Lazy A*.

- Goldberg and Harrelson's ALT work already combines A* with landmark bounds and landmark subsets.
- Sturtevant et al. define differential heuristics as a maximum over pivots and study placement/count tradeoffs.
- Tolpin et al. state that extending Lazy A* from two to multiple heuristics is straightforward.
- The supplied heuristic-stacking report implements a multi-stage Lazy-A* idea in another domain.
- Selective Max and Rational Lazy A* make adaptive decisions; this project uses a fixed schedule.
- Rayner et al. study fixed heuristic-subset selection; choosing a prefix is not itself novel.

The defensible contribution is:

> A controlled, map-disjoint empirical study of node-level progressive evaluation of a prospectively fixed differential-landmark prefix in optimal four-neighbor grid A*, with exact decomposition of saved heuristic work versus added OPEN work and explicit preprocessing amortization.

A targeted primary-source search did not locate an experiment exactly matching that intersection. This is an absence-of-find, not proof that none exists; the report must say “we study” or “we evaluate,” never “we are the first.”

## Non-overlap with already chosen projects

The locally supplied chosen-project register was checked and rechecked on
2026-08-12. No listed project studies fixed nested differential-landmark
evaluation in Lazy A*. The closest entries use 15-puzzle heuristics,
bidirectional search, memory-bounded search, FOCAL priorities,
IDA*/prediction, games, Colored Tubes, WMM, or RAG. This project remains
unidirectional, optimal, single-agent grid A* and uses none of those
interventions. The register is not redistributed in the public artifact.

## Disposable feasibility signal - not scientific evidence

An internal disposable probe used four synthetic 101x101 maps, 32 landmarks, `K=4`, and five timing repeats. All four methods matched BFS. Relative to ordinary Lazy A*, staging was approximately 4.7% slower on two random maps and 11.8% faster on two maze maps, while remainder-stage calls fell by approximately 2.9% and 10.1%.

These observations show only that the implementation idea is feasible and a crossover is plausible. They are not project results: the sample is tiny, synthetic, explored before protocol freeze, and timed in a noisy regime. Files under `tmp/candidate_prototypes/` must not enter the final results, sample sizes, tests, or conclusions.

## Frozen production protocol

- Development uses four source-`train` maps: `maze-128-128-1.map`, `random-64-64-10.map`, `room-64-64-8.map`, and `warehouse-20-40-10-2-1.map`.
- Sealed evaluation uses four source-`validation` maps and four source-`holdout` maps listed in `PROJECT_SPEC.md`; no map overlaps development.
- For every map, scenario files 1 through 4 are read in source order. Development selects the first 10 valid rows per file (40 per map, 160 total); evaluation selects the first 25 (100 per map, 800 total).
- Each map receives 32 deterministic landmarks. The first is the row-major-first traversable state; each later landmark maximizes distance to the selected set, with row-major tie breaking.
- Each query/method has one warm-up and eight timed repetitions. The base method order is left-rotated by repetition, so each method occupies every within-block position exactly twice for every query. This gives exactly `960 * 4 * 9 = 34,560` searches.
- The checked-in authority is [`configs/progressive_landmarks_v2.json`](../configs/progressive_landmarks_v2.json). Protocol and core code live under [`src/python/progressive_landmarks`](../src/python/progressive_landmarks/).
- Protocol v2 prospectively supersedes the retained but formally unlaunched v1. The correction was made before formal development or sealed evaluation because seven rotations did not balance method positions within a query; v1 smoke artifacts are non-formal and excluded from evidence.

## Hard scope promise

One Python codebase, four algorithms, 12 public maps, fixed `K=4` and `M=32`, one sealed evaluation, and an 8-12 page report. No CBS, minimum vertex cover, MAPF, machine learning, rational-policy calibration, C++ solver extension, parallelism, dynamic maps, or new landmark-placement algorithm.

## Completed-study snapshot

The frozen study retained all 800 sealed queries. Every method matched BFS and the full-landmark traces were invariant. Staging saved a median 3.10% of pivot evaluations and 3.02% of physical table reads relative to ordinary Lazy A*, but the paired runtime ratio was 1.0543 with a hierarchical-bootstrap 95% interval `[0.9890, 1.1016]`. The empirical contribution is therefore the exact saved-work/additional-OPEN decomposition and topology-dependent crossover, not a universal speedup claim. The authoritative paths, hashes, mechanical rerun history, and no-tuning statement are in [`EXPERIMENT_PROVENANCE.md`](EXPERIMENT_PROVENANCE.md).
