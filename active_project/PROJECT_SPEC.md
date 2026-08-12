# Project Specification: Progressive Landmark Evaluation in A*

Version: frozen protocol v2, 2026-08-12  
Status: implementation, formal development, sealed evaluation, analysis, and report complete; student identities and public immutable repository metadata pending

## 1. Purpose and authority

This specification turns the selected topic into a bounded, reproducible
experiment. It derives its deliverables from the current project instructions,
distributed through the course site: literature and motivation, detailed
reproducible methodology, experimental tables and figures with analysis,
conclusions and limitations, and a code link. Prior-year reports calibrate
ambition but do not override the current instructions. Course-distributed
instructions and example reports are not redistributed in the public artifact.

The machine-readable authority is [`configs/progressive_landmarks_v2.json`](../configs/progressive_landmarks_v2.json). [`src/python/progressive_landmarks/protocol.py`](../src/python/progressive_landmarks/protocol.py) validates that configuration and the checksum-pinned source snapshot and materializes the exact canonical plan. [`src/python/progressive_landmarks/core.py`](../src/python/progressive_landmarks/core.py) is the shared search and heuristic implementation. If prose and validated configuration disagree, execution must stop and the prose must be repaired; the runner may not silently reinterpret either source.

Protocol v2 prospectively supersedes the retained v1 before any formal development or sealed-evaluation run. The sole scientific change is eight rather than seven timed rotations, balancing each method exactly twice in each within-block position for every query. Non-formal v1 smoke artifacts remain excluded from the evidence chain.

Only artifacts created for this active project may support its scientific claims. Earlier MVC/CBS solvers, configurations, results, reports, and logs are frozen side material. The disposable landmark probe is feasibility-only and is excluded from final analysis.

## 2. Search domain

For each Moving AI map `m`, construct an undirected unit-cost graph `G_m=(V_m,E_m)`:

- `V_m` contains every cell marked `.`. Symbols `@` and `T` are blocked; other symbols fail closed.
- `E_m` contains horizontal and vertical neighbor pairs only; every edge costs 1.
- The graph may have multiple connected components. It is not reduced to its largest component.
- A query is valid only when start and goal are distinct traversable cells in the same four-neighbor component.
- There are no diagonal moves or wait actions.

The distance field stored in a `.scen` row was produced under other movement conventions and is parsed only for syntax. It is never used as the oracle. An independent unit-cost four-neighbor BFS supplies the reference cost for each selected query.

## 3. Fixed differential-landmark heuristics

Let `t` be a query goal and `d(x,y)` the exact shortest-path distance when the two cells are connected.

### 3.1 Base and landmark bounds

```text
H_0(v,t)   = |x_v-x_t| + |y_v-y_t|
phi_i(v,t) = |d(l_i,v) - d(l_i,t)|
H_k(v,t)   = max(H_0(v,t), phi_1(v,t), ..., phi_k(v,t)).
```

If a map-level landmark is in another connected component, its two distances are unreachable and that term is ignored for the query. Each landmark distance row is stored as immutable little-endian unsigned 32-bit values with an explicit unreachable sentinel.

### 3.2 Deterministic map-level landmark order

The full count is fixed at `M=32`; the staged prefix is fixed at `K=4`. Neither is tuned.

1. Enumerate all traversable states in row-major order and choose the first as `l_1`.
2. For each later landmark, maximize the minimum distance to the already selected set.
3. An unrepresented connected component has infinite priority, so components receive a representative before an already represented component receives another, subject to the 32-landmark budget.
4. Break every distance tie by row-major order.

Placement is query-independent. Record the map hash, ordered coordinates, placement version, table hash, build time, and packed payload bytes.

### 3.3 Admissibility, consistency, and nesting

Reverse triangle inequality gives

```text
|d(l_i,v)-d(l_i,t)| <= d(v,t).
```

For an edge `(v,w)` of cost 1,

```text
phi_i(v,t) <= 1 + phi_i(w,t).
```

Each landmark term and Manhattan distance is therefore admissible and consistent in its component. A pointwise maximum of consistent heuristics remains consistent, giving

```text
H_0 <= H_4 <= H_32 <= d(v,t).
```

Property tests complement, rather than replace, this proof.

## 4. Algorithms

### 4.1 Shared graph-search contract

All four modes use one A* engine and differ only in heuristic-evaluation schedule.

- Per-state search labels hold best `g`, parent, version, ordinal, completed stage, and current `h`.
- Heuristic stage values are cached by state within a query and reused if a later strict better-`g` relaxation revisits that state; heuristic values do not depend on `g`.
- OPEN entries are ordered lexicographically by `(f, -g, ordinal, row-major index, version, stage)`. The ordinal precedes row-major state ID. A successful relaxation receives a fresh ordinal, while heuristic promotion/requeue preserves the existing ordinal.
- Entries with stale version, stage, `g`, ordinal, or live-state status are discarded on pop.
- Any landmark mode fully evaluates the start to `H_32` before its first OPEN insertion. For `staged`, this performs prefix then suffix computation without start requeues.
- On any valid pop, the goal test occurs before expensive refinement. Since every goal-stage value is zero, the popped goal is safe to return. The goal is not counted as expanded and does not enter the expansion digest because `expanded` means that successors were enumerated.
- A non-goal state is expanded only after completing its mode's required final stage. A required refinement advances and requeues even if `h` does not increase.
- CLOSED reopening is implemented for a later strict better `g`; reopen counts are retained even when consistency makes them zero.
- `generated` counts every successor enumerated. `relaxations` counts accepted first discoveries and strict improvements. `expanded` counts non-goal states whose successors are enumerated.

### 4.2 Frozen methods

| Config mode | Start | Newly accepted non-start state | Valid non-goal pops | Expansion stage |
|---|---|---|---|---|
| `manhattan` | compute `H_0` | compute/cache `H_0` | expand | `H_0` |
| `eager_full` | compute `H_32` | compute/cache `H_32` | expand | `H_32` |
| `lazy_full` | compute `H_32` | compute/cache `H_0` | if needed compute `H_32`, preserve ordinal, requeue; expand on a later full-stage pop | `H_32` |
| `staged` | compute `H_4`, then the remaining 28 terms to obtain `H_32` | compute/cache `H_0` | if needed compute `H_4`, preserve ordinal, requeue; then compute only the remaining 28 terms, preserve ordinal, requeue; expand on a later full-stage pop | `H_32` |

There is no OPEN bypass, rational bypass, learned policy, or adaptive prefix.

### 4.3 Correctness and trace invariant

Every live key is a lower bound on a solution through its state, and refinement only preserves or increases that bound. Any unrefined entry whose lower key can compete is popped, refined, and requeued before a more expensive full-stage non-goal is expanded. A valid popped goal has `h=0` at every stage and therefore certifies an optimal cost. Finite maps, positive costs, stale-entry rejection, and reopening give completeness and optimality for valid queries.

Because `eager_full`, `lazy_full`, and `staged` ultimately use exactly `H_32` and share the stable final ordering, their successor-enumeration digests must match. This is a tested implementation invariant, not an assumption; any unexplained mismatch blocks experiments.

## 5. Frozen data split and query selection

Source inputs are the locally archived, checksum-pinned [Moving AI pathfinding benchmarks](https://movingai.com/benchmarks/). The source manifest and complete payload checksum inventory are revalidated before plan construction.

### 5.1 Map-disjoint split

| Experiment split | Source split | Family | Map |
|---|---|---|---|
| development | train | maze | `maze-128-128-1.map` |
| development | train | random | `random-64-64-10.map` |
| development | train | room | `room-64-64-8.map` |
| development | train | warehouse | `warehouse-20-40-10-2-1.map` |
| sealed evaluation | validation | maze | `maze-128-128-2.map` |
| sealed evaluation | validation | random | `random-32-32-20.map` |
| sealed evaluation | validation | room | `room-64-64-16.map` |
| sealed evaluation | validation | warehouse | `warehouse-10-20-10-2-1.map` |
| sealed evaluation | holdout | maze | `maze-128-128-10.map` |
| sealed evaluation | holdout | random | `random-32-32-10.map` |
| sealed evaluation | holdout | room | `room-32-32-4.map` |
| sealed evaluation | holdout | warehouse | `warehouse-10-20-10-2-2.map` |

The same four topology families occur on both sides, but no map file overlaps.

### 5.2 Exact source-order query rule

For every map, independently process the four random scenario files with indices 1, 2, 3, and 4. Within each file:

1. Read rows in original source order after the header.
2. Validate map identity and dimensions and parse all nine tab-separated fields.
3. A row is valid when its endpoints are distinct traversable cells in the same four-neighbor component.
4. Select the first 10 valid rows for a development file or the first 25 valid rows for a sealed-evaluation file.
5. Do not hash-sort, resample, or substitute rows. A duplicate `(map,start,goal)` anywhere in the selected plan causes plan construction to fail closed rather than being silently removed.
6. Compute the independent BFS oracle; ignore the upstream distance value.

This yields:

- development: `4 maps * 4 files * 10 = 160` queries, 40 per map and 40 per family;
- sealed evaluation: `8 maps * 4 files * 25 = 800` queries, 100 per map and 200 per family;
- total: 960 unique queries from 48 scenario files.

The canonical plan binds every map/scenario hash, query source line, split identity, method order, and parameter and is content-addressed by SHA-256.

## 6. Development and sealed-evaluation discipline

The production values `K=4`, `M=32`, one warm-up, eight timed repetitions, method rotation, maps, and query rules are already fixed in protocol v2. Development is for runner debugging, invariant validation, timing diagnostics, and failure repair—not parameter selection. In particular, there is no `K` tournament.

Sealed-evaluation outcomes may not trigger parameter changes, new methods, map/query removal, or undocumented reruns. A code defect discovered during execution must be documented, repaired, retested, and followed by a clean rerun under a new identified artifact; failed attempts remain in the audit trail.

## 7. Exact execution protocol

- Language: Python 3.12; production package: [`src/python/progressive_landmarks`](../src/python/progressive_landmarks/).
- Each `(query,method)` receives exactly one warm-up and eight timed repetitions.
- Base method order is `[manhattan, eager_full, lazy_full, staged]`. Warm-up uses rotation 0. Timed repetition `r` left-rotates that list by `r`, for `r=0,...,7`. Each method therefore appears exactly twice in each of the four within-block positions for every query.
- Every search begins with fresh mutable search state and a fresh per-query heuristic cache. Immutable per-map landmark tables are shared.
- Process startup, parsing, landmark construction, and BFS-oracle work are outside search-only timing.
- Primary timing uses `perf_counter_ns` around the complete search and disables per-stage timing instrumentation. Optional diagnostic stage timing must be clearly separated from primary runs.
- Retain all eight raw timed observations and use their ordinary even-sample median—the arithmetic mean of the fourth and fifth observations after sorting—as the per-query timing observation; repetitions are not independent experimental units.
- Execute on one recorded machine and preserve OS, CPU, Python version, source/config/plan hashes, command arguments, timestamps, input hashes, and landmark-table hashes.
- Protocol v2 declares no per-query timeout. Failures are recorded and investigated; results are never silently dropped or replaced.

The exact planned workload is:

| Split | Queries | Methods | Repetitions including warm-up | Searches |
|---|---:|---:|---:|---:|
| Development | 160 | 4 | 9 | 5,760 |
| Sealed evaluation | 800 | 4 | 9 | 28,800 |
| **Total** | **960** | **4** | **9** | **34,560** |

Of the total, 3,840 are warm-up searches and 30,720 are timed searches.

## 8. Measurements

### 8.1 Correctness and search work

- found/status, returned cost, BFS cost, equality flag, validated path, and successor-enumeration digest;
- `expanded`, `generated`, `relaxations`, `reopened`, and `unique_discovered`;
- `pops`, `stale_pops`, and valid pops (`pops-stale_pops`);
- `requeues`, derived pushes (`1+relaxations+requeues`), `max_open_entries`, and `max_live_states`.

### 8.2 Heuristic work

- `manhattan_calls`, `prefix_calls`, `suffix_calls`, `full_calls`, and `heuristic_cache_hits`;
- `pivot_evaluations`: landmark terms actually evaluated by a stage;
- `distance_table_reads`: physical table-value reads, distinguished from pivot evaluations. Each landmark mode prefetches one goal-distance value per effective landmark once per query; each evaluated landmark term then reads the state value. Reusing a cached stage performs neither operation.

Primary mechanism comparisons between `staged` and `lazy_full` include:

```text
pivot_saving = 1 - pivot_evaluations(staged) / pivot_evaluations(lazy_full)
read_saving  = 1 - distance_table_reads(staged) / distance_table_reads(lazy_full)

open_operations = derived_pushes + pops
extra_open_work = open_operations(staged) - open_operations(lazy_full).
```

Raw authoritative counters take precedence over formulas. The report must not call pivot evaluations “table reads.”

### 8.3 Timing, preprocessing, and resources

- all eight search-only `search_ns` observations and their per-query even-sample median;
- optional `stage_ns` only in separately labeled diagnostics, zero/disabled in primary timing;
- landmark build time per map and packed distance-table bytes;
- peak physical OPEN entries and peak live states.

Runtime is noisy and implementation-dependent. Exact mechanism counts are co-primary evidence used to explain timing rather than retrofit it.

## 9. Preprocessing amortization

Landmark tables are built once per map and shared by the three landmark modes. Report search-only results first, then the predeclared repeated-query model for `Q in {1,10,100,1000}`:

```text
amortized_time_a(Q) = mean_search_time_a + build_time_a / Q.
```

Use `build_time=0` for Manhattan and the same measured 32-landmark build cost for all landmark modes. Also report raw build time and packed bytes. Do not charge preprocessing once per query or hide it.

## 10. Analysis plan

The primary comparison is paired `staged` versus `lazy_full` on all 800 sealed-evaluation queries.

- Verify costs and full-landmark digests before performance analysis.
- Report per-map medians and IQRs for time ratio, pivot/read savings, suffix-stage avoidance, and extra OPEN work.
- Report family-level and overall paired distributions; never pool repetitions as independent observations.
- Estimate the overall median paired log-time ratio with a 95% hierarchical bootstrap interval by resampling maps and then queries within maps using 10,000 fixed-seed replicates.
- Plot per-query read saving against log-time ratio, colored by family; report Spearman association descriptively without causal language.
- Compare all three full-landmark modes through exact trace invariance and heuristic-work/OPEN-work decomposition.
- Compare Manhattan with landmark modes using search-only time and the predeclared amortization curves.
- Retain every planned query and report every failure. Use no post-hoc exclusions.

Planned figures: stage schematic; per-map paired time ratios; saved-work versus extra-OPEN scatter; family mechanism decomposition; preprocessing-amortization curves. Planned tables: manifest summary; correctness/invariants; per-map performance; hypothesis outcomes; limitations.

## 11. Threats to validity

- **Construct:** Packed-array reads and heap operations have Python- and hardware-specific costs. Exact counts are portable; runtime crossover points may not be.
- **Internal:** Lazy variants can differ accidentally through tie breaking, duplicates, caching, start initialization, or goal timing. The shared engine, fixed OPEN tuple, BFS oracles, trace digests, and counter fixtures control this.
- **Timing:** Short searches are sensitive to scheduling and cache state. rotated blocking, warm-up, repeated medians, raw observations, disabled stage timers, and count-based analysis mitigate but do not eliminate noise.
- **External:** Twelve maps in four grid families do not represent all graphs, costs, or landmark implementations. Claims remain conditional on this protocol.
- **Data semantics:** Stored Moving AI scenario costs may assume diagonal motion; only endpoints are reused.
- **Landmark choice:** Farthest-first order can make the four-landmark prefix unusually strong or weak. Placement is fixed and not compared with alternatives.
- **Split:** Families appear on both sides although maps are disjoint; this is map-disjoint evaluation within known families, not unseen-domain generalization.
- **Novelty:** Landmark subsets and multi-stage Lazy A* are prior ideas. The contribution is the controlled intersection and empirical decomposition.

## 12. Hard exclusions

No MVC, CBS, MAPF solving, multi-agent constraints, eight-neighbor motion, weighted terrain, dynamic replanning, bidirectional search, IDA*, bounded-suboptimal search, FOCAL, pattern databases, learned heuristic selection, Selective Max implementation, Rational Lazy-A* calibration, adaptive per-node `K`, development-set `K` selection, landmark-placement tournament, landmark compression, GPU/parallel implementation, or C++ optimization.

No method may be added after sealed evaluation is opened. Extensions belong in future work.

## 13. Acceptance gates

### Gate A - literature and protocol: complete

- Instruction/example/chosen-project audits and the proposal-level primary-source boundary are recorded.
- The strict protocol builder validates configuration shape, split identity, source hashes, exact source-order query counts, duplicate rejection, balanced rotation, and the 34,560-run plan.
- Protocol tests reproduce a byte-deterministic canonical plan and fail closed under tampering.

### Gate B - search core: complete

- Strict map parsing, connected components, BFS, deterministic landmarks, packed tables, nested estimates, all four modes, stale entries, reopening, path validation, counters, cache reuse, and trace digests are implemented under `src/python/progressive_landmarks`.
- Exhaustive/hand-map tests establish BFS equality, heuristic monotonicity/admissibility/consistency, deterministic counters, and matching full-landmark traces.
- Standard Lazy start initialization, goal-before-refinement, ordinal-preserving promotions, and goal exclusion from expansion/digest have regression coverage.

### Gate C - runner and development validation: complete

- The runner binds the canonical plan, builds/verifies BFS oracles and landmark tables, executes rotated warm-up/timed blocks, preserves raw rows and environment metadata, and validates matrix completeness.
- All 160 development queries and 5,760 searches completed under the corrected authoritative code aggregate.
- The external gate replayed source maps, BFS, paths, landmark tables, deterministic methods, counters, schedules, and digests and recorded `selection_performed=false` before authorizing evaluation.
- The authoritative development manifest is `data/results/progressive_landmarks_v2_rerun1/development/manifest.json`, SHA-256 `1d993d4ac7106730f5cddbfe7b5c15d979e8e4da9652b2a2d9f67b781d68814d`.

### Gate D - sealed evaluation and analysis: complete

- All 800 queries and 28,800 searches executed under unchanged protocol v2, and every planned query was retained.
- The exact matrix reconciles with zero BFS-cost mismatch and zero unexplained full-landmark trace mismatch.
- Immutable raw data, authorization, manifests, hashes, source/code/environment bindings, query tables, analysis tables, and figures are preserved.
- The strict analysis loader recomputes aggregates, hypothesis rows, self-hashes, and the fixed-seed hierarchical bootstrap from query-level evidence.
- The authoritative evaluation and analysis manifest SHA-256 values are `edaea56bb3aaa0b55b903e6dcde9692217a9d24a77da6a66bb52c1e583e62d53` and `47c3244fedcd52d2da0fa6f4889e0cb0cdb3289306f8b0ca69792149096c66df`.

### Gate E - technical submission audit complete; administrative metadata pending

- The report contains motivation/literature, reproducible methodology, readable results, direct answers to RQ1-RQ4, limitations, conclusions, future work, and exact reproduction instructions.
- Citation, novelty, collision, requirement-by-requirement, and rendered-PDF visual checks pass; the reproducibility audit has no warnings or failures.
- A working public code link and the two students' names/IDs must be inserted before handoff.

No positive performance result is required. Correct, reproducible, well-explained negative evidence satisfies the research objective.

## 14. Locked observed outcomes

The complete artifact and rerun history is recorded in [`EXPERIMENT_PROVENANCE.md`](EXPERIMENT_PROVENANCE.md). The locked sealed results are:

- H1 passed on 800/800 queries for BFS cost and full-landmark trace invariance.
- H2 was supported: median pivot-evaluation saving was 0.0309758772 and median physical-table-read saving was 0.0301724138 for `staged` versus `lazy_full`.
- H3 is descriptive rather than a positive speed claim: the paired time ratio was 1.0543002344 with a map-then-query hierarchical-bootstrap 95% interval `[0.9890432531, 1.1015852400]`. Maze maps favored staging in median time, while random, room, and warehouse maps did not.
- RQ4's descriptive amortization diagnostic found that preprocessing dominated one-off use and landmark methods became competitive only with high query reuse. This is not a fourth registered pass/fail hypothesis; the generated hypothesis table contains H1--H3.

The first formal raw attempt is preserved but superseded because a tuple/list integration mismatch caused analysis to fail closed before statistical output. A single representation-normalization fix changed only the analysis boundary; because run manifests bind the complete code aggregate, both splits were mechanically rerun. Protocol, methods, data, hypotheses, and estimands were unchanged, and no tuning or post-hoc exclusion occurred.
