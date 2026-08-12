# Literature Map: Progressive Landmark Evaluation in A*

Last primary-source check: 2026-08-12  
Status: literature/collision audit, empirical study, page-level report citation audit, and final local register recheck complete under protocol v2

## Claim discipline

The defensible contribution is a controlled empirical study of one combination: a prospectively fixed prefix of differential-landmark tables, evaluated progressively per node inside optimal Lazy A*, on map-disjoint four-neighbor Moving AI grids. The contribution is not any ingredient independently.

Allowed wording:

> We evaluate whether an intermediate fixed four-landmark prefix saves enough remaining landmark work to compensate for an additional OPEN cycle, and characterize the tradeoff across four map families.

Forbidden without stronger evidence:

- “We introduce landmark heuristics.”
- “We are the first to use landmark subsets.”
- “We invent multi-stage Lazy A*.”
- “Our method is rational or adaptive.”
- “Progressive evaluation is universally faster.”

A targeted primary-source search did not locate an experiment exactly matching this fixed-prefix, node-level staging question. That observation is not an exhaustive novelty proof. The report must say “we did not locate,” not “no prior work exists.”

Protocol v2 prospectively supersedes the formally unlaunched v1 solely to balance timing-order positions; it does not change the method, data, hypotheses, or literature boundary. Non-formal v1 smoke artifacts are not project evidence.

The completed result does not broaden the novelty claim: staging saved exact landmark work but did not establish an overall runtime improvement. The report must present the 1.0543 paired ratio and interval crossing 1 as mixed, topology-dependent evidence, not as a new universally faster algorithm. The corrected formal rerun changed only tuple/list normalization at the analysis boundary and did not change any literature-facing scientific claim; see [`EXPERIMENT_PROVENANCE.md`](EXPERIMENT_PROVENANCE.md).

## Primary sources and what each establishes

### 1. A* foundation

Hart, Nilsson, and Raphael (1968), “A Formal Basis for the Heuristic Determination of Minimum Cost Paths.” [IEEE DOI](https://doi.org/10.1109/TSSC.1968.300136).

- Supports `f=g+h`, admissible heuristic search, and the foundational A* framing.
- Does not alone support every modern graph-search implementation detail.

Dechter and Pearl (1985), “Generalized Best-First Search Strategies and the Optimality of A*.” [ACM DOI](https://doi.org/10.1145/3828.3830).

- Supports careful optimality and optimal-efficiency qualifications and the importance of consistency and tie/order assumptions.
- Use for background and correctness discussion, not novelty.

### 2. Landmark lower bounds and ALT

Goldberg and Harrelson (2005), “Computing the Shortest Path: A* Search Meets Graph Theory.” [Microsoft Research record](https://www.microsoft.com/en-us/research/publication/computing-the-shortest-path-a-search-meets-graph-theory-2/) and [author/publisher-hosted PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2005/01/soda05.pdf).

- Establishes A* lower bounds from landmarks and the triangle inequality, landmark preprocessing, active landmark sets, and optimal shortest paths.
- This project does not introduce ALT, landmark preprocessing, triangle-inequality bounds, or fixed/active landmark subsets.
- ALT emphasizes large road networks and directed-graph bounds; this project uses the simpler undirected-grid differential form.

### 3. Differential heuristics and pivot count/placement

Sturtevant, Felner, Barrer, Schaeffer, and Burch (2009), “Memory-Based Heuristics for Explicit State Spaces.” [Official IJCAI PDF](https://www.ijcai.org/Proceedings/09/Papers/107.pdf).

- Establishes the undirected differential heuristic `max_l |d(l,v)-d(l,t)|`, `k` complete single-source searches, `O(k|V|)` build/storage, canonical-state placement schemes, and pivot-count tradeoffs.
- Varying landmark count, maximizing over landmarks, farthest-style placement, and memory/preprocessing tradeoffs are prior art.
- Direct design use: formula, proof intuition, deterministic farthest-first placement, and build/memory reporting.

### 4. Selecting fixed heuristic subsets

Rayner, Sturtevant, and Bowling (2013), “Subset Selection of Search Heuristics.” [Author-hosted primary PDF](https://webdocs.cs.ualberta.ca/~bowling/papers/13ijcai-hsubset.pdf) and [publication record](https://webdocs.cs.ualberta.ca/~bowling/publications/b2hd-13ijcai-hsubset.html).

- Establishes fixed-subset selection from many admissible heuristics as an existing optimization problem and shows that heuristic interactions matter.
- Choosing a subset or prefix is not novel here. This project prospectively fixes the farthest-first order, `K=4`, and `M=32`; it performs no development-set subset selection.
- Rayner et al. optimize which subset is retained. This experiment asks when a fixed retained prefix should be used as an intermediate per-node stage before the fixed remainder.

### 5. Lazy A* and multiple stages

Tolpin, Beja, Shimony, Felner, and Karpas (2013), “Toward Rational Deployment of Multiple Heuristics in A*.” [Official IJCAI PDF](https://www.ijcai.org/Proceedings/13/Papers/106.pdf) and [arXiv record](https://arxiv.org/abs/1305.5030).

- Establishes Lazy A*: fully evaluate the initial state, generate later states with a cheap admissible heuristic, evaluate an expensive heuristic when a non-goal reaches the top of OPEN, and reinsert it. The valid popped goal is checked before an expensive evaluation.
- Establishes the central tradeoff: expensive evaluations saved on surplus nodes versus added OPEN operations.
- Explicitly describes extension to multiple heuristics as straightforward, so a three-stage schedule alone is not a defensible novelty claim.
- Introduces Rational Lazy A*, which may skip an expensive heuristic using value of information. This project always completes all fixed stages before non-goal expansion and is not RLA*.

Karpas, Betzalel, Shimony, Tolpin, and Felner (2018), “Rational Deployment of Multiple Heuristics in Optimal State-Space Search.” [Publisher DOI](https://doi.org/10.1016/j.artint.2017.11.001).

- Establishes the archival treatment of rational deployment and contextual decision rules.
- This project has no learned probabilities, value-of-information calibration, bypass policy, or rational-optimality claim.

### 6. Selective Max and learned heuristic selection

Domshlak, Karpas, and Markovitch (2012), “Online Speedup Learning for Optimal Planning.” [JAIR DOI](https://doi.org/10.1613/jair.3676) and [Technion publication record](https://csaws.cs.technion.ac.il/~shaulm/papers/abstracts/Karpas-2012-OSL.html).

- Establishes Selective Max as learned state-level heuristic choice trading evaluation cost against search effort.
- This project uses a fixed schedule, performs no classification or online learning, and makes no state-dependent selection decision.

### 7. Dynamic-heuristic correctness context

Christen, Pommerening, Büchner, and Helmert (2025), “A Formalism for Optimal Search with Dynamic Heuristics.” [Official ICAPS record](https://ojs.aaai.org/index.php/ICAPS/article/view/36098) and [paper PDF](https://ojs.aaai.org/index.php/ICAPS/article/download/36098/38252).

- Establishes a modern framework for dynamically refined heuristics, including lazy evaluation and an optimality theorem under dynamic admissibility.
- Design consequence: every current key remains admissible and the implementation supports reopening.
- It is correctness context, not evidence that this landmark-prefix schedule is novel or fast.

### 8. Benchmark source

Moving AI Lab, “Pathfinding Benchmarks.” [Official benchmark page](https://movingai.com/benchmarks/).

- Establishes the public grid maps/scenarios and their use for comparable pathfinding studies.
- Only scenario endpoints are reused. Stored scenario distances are ignored because the experiment uses a declared four-neighbor model and independent BFS oracles.

## Course-local sources

- Current project instructions: authoritative requirements and permitted
  project directions, distributed through the course site.
- *Heuristic Stacking in Sliding Tile Puzzles*: closest supplied scope analogue
  and evidence that multi-stage Lazy A* is already present in course example
  material.
- *Early vs Late A Comparison*: supports a mechanism-first study of a narrow
  A* implementation choice and careful runtime interpretation.
- Accompanying example note: the examples are from prior years and do not
  establish this year's requirements.
- Locally supplied chosen-project register: final collision recheck completed
  2026-08-12; no progressive differential-landmark staging project appears in
  the 18 listed topics.

The course-distributed instructions, example reports, note, and register are
not redistributed in the public research artifact.

Course examples are not scholarly citations for algorithmic facts. The report must cite the primary papers above for those claims.

## Novelty matrix

| Element | Already established | This project's residual question |
|---|---|---|
| Landmark triangle bound | ALT (2005) | None; reused. |
| Differential maximum over pivots | Sturtevant et al. (2009) | None; reused. |
| Pivot count, placement, and subset selection | DH and subset-selection literature | Placement, `K=4`, and `M=32` are fixed prospectively rather than tuned. |
| Lazy evaluation of an expensive heuristic | Tolpin et al. (2013) | Apply it to a full differential-landmark set on four-neighbor public grids. |
| More than two Lazy-A* stages | Explicitly straightforward in Tolpin et al.; present in the supplied example | Not claimed as novel. |
| Learned/rational heuristic choice | Selective Max and RLA* | Intentionally excluded. |
| Fixed nested prefix as an intermediate filter | Ingredients exist separately | Measure the heuristic-work/OPEN-work crossover under one locked, map-disjoint protocol. |

## Chosen-project collision assessment

No registered project studies Lazy-A* evaluation staging over a fixed nested differential-landmark prefix. The nearest projects concern 15-puzzle heuristics, FOCAL priorities, bidirectional grid/voxel search, memory-bounded search, or learned prediction. The active topic must remain within fixed optimal single-agent grid search; adding bidirectionality, bounded-suboptimal search, PDBs, learning, or memory-bound control would create avoidable overlap.

## Final literature and collision audit

The verified citation ledger, targeted exact-combination search, novelty-language
review, and local chosen-project-register recheck are complete. No exact prior
art or registered course-project collision was found. The report therefore uses
the conservative empirical-combination wording above and does not claim that
landmarks, subsets, or multi-stage Lazy A* are new.
