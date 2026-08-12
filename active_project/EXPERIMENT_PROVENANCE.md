# Experiment Provenance: Progressive Landmark Evaluation in A*

Updated: 2026-08-12

This ledger identifies the only authoritative scientific bundle, records the
superseded first attempt, and separates completed evidence from pending
administrative work. All SHA-256 values are lowercase hashes of file bytes
unless explicitly labeled as a canonical JSON self-hash.

## Frozen design authority

| Item | SHA-256 |
|---|---|
| `configs/progressive_landmarks_v2.json` | `850176ee199020920e9a425db2fced560c776993148775472e2098e33c125410` |
| Canonical v2 plan JSON | `01aa82ec39842555d2e24216ccd93b9197f498daebf30e0e61aedd4fee5bd523` |
| `src/python/progressive_landmarks/protocol.py` | `baee6d0ab05b0fd46594364b917370c1108feba629ec3f3c54f5bf5cd21f376b` |
| Moving AI source `CORPUS_MANIFEST.json` | `c4425b97a0ed60cf389c35d7bda9a2756da89bd687431593e10e89b3757e1bb9` |
| Moving AI source `SHA256SUMS` | `099900be19063488f8d09a77e691977c2a1182bbc2f7e33dc00c2c7e22c1a47f` |

Protocol v2 fixed 12 maps, 48 scenario files, 960 queries, `K=4`, `M=32`,
four methods, one warm-up, and eight timed rotations. Its total matrix is
34,560 searches: 5,760 development and 28,800 sealed evaluation. The v2 plan
prospectively replaced the formally unlaunched v1 only to balance each method
twice in every within-query timing position.

## Corrected authoritative bundle

The authoritative root is:

`data/results/progressive_landmarks_v2_rerun1/`

### Development result

| File | Bytes | Records | SHA-256 |
|---|---:|---:|---|
| `development/run.json` | 11,834 | 1 | `ec98e11f1a5a94eba1cf09f0038fc94185b18754d2a855b9fc3bb316d3d3bf0d` |
| `development/maps.json` | 8,323 | 4 | `f426aee15cee7e3dd83a448b0fb930b8c203403cbf70f6d60ccbe706d5a001f1` |
| `development/queries.jsonl` | 4,404,376 | 160 | `6dc517fd7b239b24ab82bfd578b2dea27b130bf9dfce9eb9d3305617518e6bb3` |
| `development/development_freeze_candidate.json` | 2,153 | 1 | `dcb98ad65e6ddb274d3cdec46454b8918db6a17a2ad46dbdfe23b7442278d679` |
| `development/manifest.json` | 944 | 1 | `1d993d4ac7106730f5cddbfe7b5c15d979e8e4da9652b2a2d9f67b781d68814d` |

The manifest records 160 complete queries, four maps, 5,760 searches, formal
status, and passed validation. The byte-identical standalone directory
`data/results/progressive_landmarks_development_v2_rerun1/` is retained as a
convenience copy, but the freeze and analysis intentionally bind the nested
path above. It is not a second experimental sample.

### External development gate and evaluation authorization

| File | SHA-256 |
|---|---|
| `development_audit.json` | `d8792f0d34ef344b9dcd7aa441b4463c38769724803f2b9e807ba992fa8beab1` |
| Audit canonical self-hash, stored as `audit_sha256` | `e632bcbeede7890de270117b547cd3d96f2f2833d4534aae11435ae859073ad1` |
| `sealed_evaluation_freeze.json` | `3bce31ce4f942eccb0a0fc18c302e47fa477503a06b466cf6d798b21340f0e72` |

The gate independently replayed the canonical plan, source maps, BFS costs,
paths, landmark tables, deterministic methods, counters, schedules, and full-
landmark trace digests. It recorded `selection_performed=false` and authorized
sealed evaluation without using development outcomes to choose a method or
parameter.

### Sealed-evaluation result

| File | Bytes | Records | SHA-256 |
|---|---:|---:|---|
| `sealed_evaluation/run.json` | 18,207 | 1 | `23e35eae365cf0780094c3e087b684b73cd26ca4a554dcc2d7539263321fc55f` |
| `sealed_evaluation/maps.json` | 16,549 | 8 | `47db3e4a2b5b05a9671f27fec65259dc95318f055763e9dacae2dda640edb2d5` |
| `sealed_evaluation/queries.jsonl` | 21,988,333 | 800 | `8ba0e1a008bcfe1e8281c98b454ff243bad1e9c367bc5f25ac35236218e63ed5` |
| `sealed_evaluation/manifest.json` | 779 | 1 | `edaea56bb3aaa0b55b903e6dcde9692217a9d24a77da6a66bb52c1e583e62d53` |

The manifest records 800 complete queries, eight maps, 28,800 searches, formal
status, and passed validation. No query was excluded.

### Analysis result

The authoritative analysis root is:

`data/processed/progressive_landmarks_analysis_v2/`

Its manifest SHA-256 is
`47c3244fedcd52d2da0fa6f4889e0cb0cdb3289306f8b0ca69792149096c66df`.

| Artifact | SHA-256 |
|---|---|
| `summary.json` | `0f6e9fc457c3b5e4cb4de54abf3762615cdca97e0ee0b04a86b49eeda4961f9a` |
| `provenance.json` | `b06d5799a84670cd184b2d5bebe547b7521ed7215ca4fd508797772701aa6544` |
| `query_metrics.csv` | `d12225dcc348aad43f33be19c9850b66e611fbc060a5b5fca87bb85c1b6acc32` |
| `map_metrics.csv` | `9ddb3fc0be7d960bbda964295e5a0cb9bd9b428d73b0e9e78d73ff27ed4a5f80` |
| `hypothesis_table.csv` | `ef07cca1b316940213163ecb05a3b0e2660f4d060ccefd7c31447bf7cf7ec04a` |
| `hypothesis_table.tex` | `70e2fdb5a49fc4ee466946b3204be0c06ccce9124d6ddf3fddb9db0f606059f7` |
| `figures/stage_schematic.png` | `bd7f89876607da6baca9e6bd87eef67a845d9f491a639ba04d64f79a6aaa3f5d` |
| `figures/stage_schematic.pdf` | `d23a640ff4d097d88db9fef7635108bf11ac6df811fc415649f36e92ca0f5a19` |
| `figures/per_map_time_ratios.png` | `d2e8fdcde7e101f1e3c827a529d8a64db1e8c1959acacdd5e31a8c12ca40dcd0` |
| `figures/per_map_time_ratios.pdf` | `3258302faab6410aa11d97b5e5fd4edcb2cd165b584523952ec2cc076ec67596` |
| `figures/saved_work_vs_time.png` | `9ebb262a492d48527d54f481aee211fe96d6a6a976ac37b27eec5f82d2c9bbec` |
| `figures/saved_work_vs_time.pdf` | `0d2083a542441d64097214100bd0226675bac80daa53f5e48456756c074fb993` |
| `figures/family_mechanism_decomposition.png` | `3ea4f5daa508de109bd2d283bff7b9e7f096eed9f83a8580bf4220419dd8773e` |
| `figures/family_mechanism_decomposition.pdf` | `8e2d666ac73a73a48d495a42e1a17c87a15477d378395b104b495b7d9c865204` |
| `figures/preprocessing_amortization.png` | `de04a64373bb8108ea5ad3b1a372149272e84332625d8db23c77492e567a487a` |
| `figures/preprocessing_amortization.pdf` | `55d37f18a2ed359961aa48826d1080e62d4f5dadff2d278eeb8ae2ca01603752` |

The manifest declares 800 query rows, eight map rows, three hypothesis rows,
and ten figures. The loader recomputes map aggregates, the fixed-seed 10,000-
replicate hierarchical bootstrap, hypothesis tables, and JSON self-hashes from
the query CSV before accepting the directory.

## Code and environment bindings

The corrected runs bind production-code aggregate SHA-256
`6412fb0e509302fed5c3f58d82b5e0dae761ffcf8da5ee7ae7aab5c11d0d0d92`.
Important individual files are:

| File | SHA-256 |
|---|---|
| `core.py` | `fe95b1bb90ad2c4df46ce068d4095694680948e5ed965e41a71c3ade6d826b38` |
| `runner.py` | `79e6922fe6cae05c5ced8877af2910c88d144f338ca8f82c041076bec7f51964` |
| `development_gate.py` | `c14aabca1c027b682aae354ff18cc6ca6aea623515bacef196dd6e94c66f76c3` |
| `analysis.py` | `9497e8d6df3964e391aef1d651152e9824f415d254504184e3177cbd7625d853` |
| Runner CLI | `87c632e593be32f2b0f4e6c7d23a8fba1258170caced332df8063204b4b6ade6` |
| Freeze CLI | `137aed37de8daa92d387e8c2f3622b5d3298ab786580bcdfe746b442a0d6c79f` |
| Analysis CLI | `35d8a8e8a44ff536f51bc4c2466e1a6d0f5d3f5e66aeee2cc35a3eb07c1705a2` |

The recorded environment self-hash is
`81b259dab46ecd63103a3a5e302c791a9b14732ad5ed4da93c1551ebf58b7830`:
Windows 11 AMD64, CPython 3.12.9, MSC 64-bit compiler, little endian, and eight
logical CPUs. `pythonhashseed` was unset; none of the scientific ordering
depends on Python hash iteration.

## Superseded formal attempt and correction

The first formal run completed both raw matrices, but the first analysis
invocation stopped before statistical output. The fail-closed analysis
integration compared an immutable tuple representation with a JSON-list
representation of the same schedule. It rejected the mismatch and did not
publish an analysis directory.

Preserved first-attempt completion markers:

| Artifact | SHA-256 | Status |
|---|---|---|
| `data/results/progressive_landmarks_development_v2/manifest.json` | `f53642241843d1708ddccb88fd43f939782e09330847e0eeaf811d1deaf40388` | superseded |
| `data/results/development_audit.json` | `e69fae4122a383f645057053abe94b9538e54814fbd74f5abc03fbc93c11a38d` | superseded |
| `data/results/sealed_evaluation_freeze.json` | `910ee0600b43e42e65983c140be190c6029c6963770dae6b0c2c72fb495ea0ba` | superseded |
| `data/results/progressive_landmarks_sealed_evaluation_v2/manifest.json` | `401acfe53bf7de5c59e64a8050f48bc22269ea28fc83a58dbd19b90a0d0ad125` | superseded |

The correction normalized tuple/list containers to the same plain JSON-like
form at the analysis boundary. The analysis file changed from SHA-256
`416a0214f468ce19dd27d389ab4550faa858590f4409c336890e45ed67185400`
to `9497e8d6df3964e391aef1d651152e9824f415d254504184e3177cbd7625d853`;
the aggregate changed from
`6f75bde2b47f877dfa6f5967038bfc0a9a6e75444c11b8e2d8e0cacfdeffee08`
to `6412fb0e509302fed5c3f58d82b5e0dae761ffcf8da5ee7ae7aab5c11d0d0d92`.
The runner, core, development gate, CLIs, protocol, configuration, canonical
plan, source data, hypotheses, and analysis parameters were byte-unchanged.

Because a formal manifest binds the complete code aggregate, both splits were
rerun rather than relabeling the first attempt. This was a mechanical integrity
rerun, not a scientific redesign. Development results were not used for
selection; sealed outcomes were not inspected to change anything; every
corrected query was retained; and no statistical artifact existed before the
fix.

## Result ledger

- H1 passed: 800/800 sealed queries matched BFS and passed full-landmark trace
  invariance.
- H2 was supported: median staged-versus-lazy savings were 0.0309758772 for
  pivot evaluations and 0.0301724138 for physical table reads.
- H3 remained descriptive: paired ratio 1.0543002344, hierarchical-bootstrap
  95% interval `[0.9890432531, 1.1015852400]`, with probability 0.053 below 1;
  the read-saving/log-time-ratio Spearman association was -0.6406108533.
- RQ4's descriptive amortization diagnostic found equal-weight mean staged time of about 1.882 s
  at `Q=1`, 195.4 ms at `Q=10`, 26.79 ms at `Q=100`, and 9.93 ms at `Q=1000`,
  versus 15.54 ms for Manhattan at every `Q` under the stated model.
- The primary runtime interval crosses 1. Timing is noisy and specific to this
  Python implementation and machine. The experiment supports saved-work and
  topology-dependent crossover claims, not a universal staged speedup.

## Verification checkpoint

Before documentation work, the complete working-archive Python suite reported
`365 passed, 1 skipped`, including historical MVC/CBS modules. The current
workspace eight-file active-public lane reports `140 passed, 2 skipped`; both
skips are optional Windows symlink-privilege branches in the runner safe-child
and metadata-finalizer tests. Syntax compilation and the focused formatter
check were clean. The scientific loaders additionally replay BFS and all
deterministic methods before performance output. Exact reproduction commands
are maintained in the root [`README.md`](../README.md).

## Remaining non-experimental work

The minimal public boundary is implemented as an explicit deterministic
152-file allowlist in `scripts/package_progressive_landmarks_release.py`. It
contains only active source, tests, protocol, public Moving AI inputs,
authoritative corrected raw evidence, analysis, references, report, and
third-party notices. It excludes course-distributed files, the superseded raw
attempt, all MVC/CBS material, and `aaai2027.sty`, whose copyright notice
prohibits redistribution without written permission. Its pinned official-kit
URL and archive/file hashes remain in `report/AAAI27_AUTHOR_KIT_PROVENANCE.md`
so a rebuilder can retrieve and verify it directly from AAAI. Two deterministic
builds of the finalized 152-file draft boundary produced byte-identical ZIPs.
The generated manifest binds every member and its own canonical self-hash; no
circular archive hash is embedded inside the archive.
A fresh extraction reported `139 passed, 3 skipped` across the full public
suite: the same two optional Windows symlink-privilege branches plus the
`test_repro_audit` historical-checkpoint case, whose historical checkpoint is
intentionally absent from the public artifact. The extracted protocol verifier
reproduced plan SHA-256
`01aa82ec39842555d2e24216ccd93b9197f498daebf30e0e61aedd4fee5bd523`;
syntax compilation, the self-contained analysis loader, and the active audit
also passed, with the latter reporting `PASS=8`, `PENDING=2` (administrative
only), `FAIL=0`, and `WARN=0`; the extraction also confirmed `aaai2027.sty` was
absent. Draft isolated validation is complete. The default final build still
refuses the remaining identity and repository placeholders.

The report, page-level citation audit, rendered-PDF QA, and chosen-project
register recheck are complete. Publication must first preserve the exact
allowlist as scientific commit A, configure and push its canonical public
HTTPS remote, and independently verify the resulting commit URL is publicly
readable. The metadata finalizer must then receive values equal to local `HEAD`
and that configured remote, pass `--dry-run`, and only then run with `--apply`.
After PDF rebuild/QA, commit B is limited to identity metadata, report
artifacts, and administrative status/provenance; it must not mutate the
authoritative raw or processed bundles. Student names/IDs and public hosting
remain pending.
