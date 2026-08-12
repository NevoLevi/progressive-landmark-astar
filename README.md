# Progressive Landmark Evaluation in A*

This repository is the reproducible artifact for a project in Ben-Gurion
University course 237-2-5513, *Search Methods in Artificial Intelligence*.
The active study asks whether a fixed intermediate differential-landmark
prefix can save heuristic work in optimal Lazy A* without changing the final
search trace, and whether those savings repay an extra OPEN cycle.

Four deterministic methods share one four-neighbor, unit-cost grid-search
engine:

- `manhattan`;
- `eager_full`, which evaluates all 32 landmarks before insertion;
- `lazy_full`, which refines Manhattan directly to all 32 landmarks; and
- `staged`, which refines `Manhattan -> 4 landmarks -> 32 landmarks`.

The protocol fixes `K=4`, `M=32`, 12 public Moving AI maps, source-order query
selection, stable tie breaking, one warm-up, and eight balanced timed rotations.
Nothing was selected or tuned from development or sealed-evaluation outcomes.
The earlier minimum-vertex-cover and CBS/MAPF program is retained in this
checkout only as frozen historical side material; it is not evidence for this
project.

## Result in one paragraph

All 800 sealed queries matched independent BFS, and the three full-landmark
methods had identical successor-enumeration digests. Relative to ordinary
`lazy_full`, `staged` saved a median 3.0976% of pivot evaluations and 3.0172%
of physical distance-table reads. It did not establish an overall runtime
improvement: the predeclared paired search-time ratio was 1.0543
(`staged/lazy_full`), with a map-then-query hierarchical-bootstrap 95% interval
of `[0.9890, 1.1016]`; only 5.3% of bootstrap replicates were below 1. The
effect was heterogeneous—maze queries favored staging, while the other three
families did not at the family-level median (one room map was slightly below
parity). Landmark construction dominated one-off use; under the
predeclared shared-map amortization model, staged landmarks became competitive
only at high query reuse. These are Python- and machine-specific timing results;
the exact work counters are the more portable mechanism evidence.

The generated evidence is in
[`data/processed/progressive_landmarks_analysis_v2`](data/processed/progressive_landmarks_analysis_v2/).
The concise project state is in [`active_project/STATUS.md`](active_project/STATUS.md),
and the complete artifact history is in
[`active_project/EXPERIMENT_PROVENANCE.md`](active_project/EXPERIMENT_PROVENANCE.md).

## Authoritative evidence bundle

Only the corrected `progressive_landmarks_v2_rerun1` bundle is authoritative.
The SHA-256 values below are hashes of the named files, except the canonical
plan hash, which is the protocol builder's canonical JSON hash.

| Artifact | Path | SHA-256 |
|---|---|---|
| Frozen configuration | `configs/progressive_landmarks_v2.json` | `850176ee199020920e9a425db2fced560c776993148775472e2098e33c125410` |
| Canonical 960-query plan | materialized from the frozen configuration | `01aa82ec39842555d2e24216ccd93b9197f498daebf30e0e61aedd4fee5bd523` |
| Development result manifest | `data/results/progressive_landmarks_v2_rerun1/development/manifest.json` | `1d993d4ac7106730f5cddbfe7b5c15d979e8e4da9652b2a2d9f67b781d68814d` |
| External development audit | `data/results/progressive_landmarks_v2_rerun1/development_audit.json` | `d8792f0d34ef344b9dcd7aa441b4463c38769724803f2b9e807ba992fa8beab1` |
| Sealed-evaluation authorization | `data/results/progressive_landmarks_v2_rerun1/sealed_evaluation_freeze.json` | `3bce31ce4f942eccb0a0fc18c302e47fa477503a06b466cf6d798b21340f0e72` |
| Sealed-evaluation result manifest | `data/results/progressive_landmarks_v2_rerun1/sealed_evaluation/manifest.json` | `edaea56bb3aaa0b55b903e6dcde9692217a9d24a77da6a66bb52c1e583e62d53` |
| Analysis manifest | `data/processed/progressive_landmarks_analysis_v2/manifest.json` | `47c3244fedcd52d2da0fa6f4889e0cb0cdb3289306f8b0ca69792149096c66df` |

The analysis manifest binds 800 query rows, eight map rows, three hypothesis
rows, and ten rendered figures. Its provenance binds the configuration, plan,
raw manifests, development audit/freeze, source snapshot, environment, and the
exact production and analysis code. Run the read-only repository checkpoint to
verify those relationships and to confirm that no active artifact relies on
the superseded first attempt:

```powershell
python scripts/repro_audit.py --active-only
python scripts/repro_audit.py --active-only --json
```

`FAIL` is an integrity error and returns exit code 1. The finalized artifact
currently reports no `PENDING` or `WARN` checks; `--require-complete` treats
either status as nonzero if a future checkout becomes incomplete. The checkpoint
does not build, launch searches, regenerate analysis, repair files, or write
scientific state.
The active artifact is the default audit scope; `--active-only` makes that
boundary explicit. Only maintainers of the larger working archive should use
`--include-historical` to add the retired MVC/CBS checks.

## Superseded first attempt

The working research archive preserves the first formal top-level v2 bundle
for the audit trail:

- `data/results/progressive_landmarks_development_v2/manifest.json`;
- `data/results/development_audit.json`;
- `data/results/sealed_evaluation_freeze.json`; and
- `data/results/progressive_landmarks_sealed_evaluation_v2/manifest.json`.

The analysis loader failed closed before it read timing outcomes or emitted any
statistical artifact: an immutable loader returned nested tuples while one
analysis integration comparison expected JSON-style lists. The only repair was
a representation-normalization change at that boundary. The protocol, query
set, `K`, `M`, methods, hypotheses, runner, search core, counters, timing design,
and analysis estimands were unchanged. Because the formal run manifests bind
the complete code aggregate, both splits were rerun into a new directory and a
new audit/freeze was issued. No parameter tuning or outcome-driven exclusion
occurred. The old bundle must never be combined with, substituted for, or cited
as the authoritative evidence. It is deliberately excluded from the minimal
public release; its completion hashes remain recorded in
[`active_project/EXPERIMENT_PROVENANCE.md`](active_project/EXPERIMENT_PROVENANCE.md),
and the active-only audit proves that no authoritative provenance points to
any of those paths.

## Environment and setup

The recorded execution environment was Windows 11 x64, CPython 3.12.9, eight
logical CPUs, and little-endian AMD64. Search execution and validation use the
Python standard library. Figure generation additionally requires Matplotlib;
Pytest is required for tests. The minimal active-project environment is frozen
in `requirements-progressive-landmarks-lock.txt`. The larger
`requirements-direct-lock.txt` is needed only for the archived MVC/CBS side
material and is not part of the landmark experiment.

From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
$Python = (Resolve-Path -LiteralPath ".venv\Scripts\python.exe").Path
& $Python -m pip install "pip==25.1.1"
& $Python -m pip install --requirement requirements-progressive-landmarks-lock.txt
& $Python -m pip install --no-build-isolation --no-deps --editable .
& $Python -m pip check
$env:PYTHONUTF8 = "1"
```

The workspace path may contain spaces and Hebrew characters. Use
`-LiteralPath` and quote derived paths. Do not rewrite paths inside immutable
manifests.

## Verification and tests

The current workspace eight-file active-public lane is `140 passed, 2 skipped`. Both
skips are optional Windows symlink-privilege branches (the runner safe-child
check and the metadata-finalizer transaction check), not failed scientific
gates. The larger working archive has additional tests for the earlier MVC/CBS
research programs, but those are outside the public artifact. Re-run the
active protocol verifier, tests, syntax compilation, and read-only artifact
audit with:

```powershell
& $Python scripts\verify_progressive_landmarks_protocol.py `
  --config configs\progressive_landmarks_v2.json `
  --repository-root . `
  --output tmp\progressive_landmarks_plan_v2.json

$ActiveTests = @(
  "tests/python/test_progressive_landmarks_core.py",
  "tests/python/test_progressive_landmarks_protocol.py",
  "tests/python/test_progressive_landmarks_runner.py",
  "tests/python/test_progressive_landmarks_development_gate.py",
  "tests/python/test_progressive_landmarks_analysis.py",
  "tests/python/test_repro_audit.py",
  "tests/python/test_progressive_landmarks_release.py",
  "tests/python/test_finalize_progressive_landmarks_metadata.py"
)
& $Python -m pytest -q @ActiveTests
& $Python -m compileall -q src\python scripts tests\python
& $Python scripts\repro_audit.py --active-only
```

A bare `pytest -q` in the larger working archive also discovers historical
MVC/CBS tests and is not the public-release validation command.

The verifier must report 12 maps, 48 scenario files, 960 unique queries, and
34,560 searches, with canonical plan SHA-256
`01aa82ec39842555d2e24216ccd93b9197f498daebf30e0e61aedd4fee5bd523`.

## Exact experiment commands

The following commands describe the authoritative corrected run. Every
scientific writer is fail-closed and refuses to overwrite its destination.
Run them only in a clean reproduction checkout where the named outputs are
absent, or substitute a separate scratch root and treat the resulting hashes
as a new reproduction rather than this frozen bundle.

```powershell
# 1. Formal development: 160 queries, 5,760 searches.
& $Python scripts\run_progressive_landmarks.py `
  --split development `
  --output data\results\progressive_landmarks_v2_rerun1\development `
  --config configs\progressive_landmarks_v2.json `
  --repository-root .

# 2. External replay audit and sealed-evaluation authorization.
& $Python scripts\freeze_progressive_landmarks_development.py `
  data\results\progressive_landmarks_v2_rerun1\development `
  --config configs\progressive_landmarks_v2.json `
  --repository-root . `
  --audit-output data\results\progressive_landmarks_v2_rerun1\development_audit.json `
  --freeze-output data\results\progressive_landmarks_v2_rerun1\sealed_evaluation_freeze.json

# 3. Sealed evaluation: 800 queries, 28,800 searches.
& $Python scripts\run_progressive_landmarks.py `
  --split sealed_evaluation `
  --output data\results\progressive_landmarks_v2_rerun1\sealed_evaluation `
  --config configs\progressive_landmarks_v2.json `
  --repository-root . `
  --freeze-manifest data\results\progressive_landmarks_v2_rerun1\sealed_evaluation_freeze.json

# 4. Replay all gates, analyze, and atomically publish figures/tables.
& $Python scripts\analyze_progressive_landmarks.py `
  data\results\progressive_landmarks_v2_rerun1\sealed_evaluation `
  --output data\processed\progressive_landmarks_analysis_v2 `
  --config configs\progressive_landmarks_v2.json `
  --repository-root . `
  --freeze-manifest data\results\progressive_landmarks_v2_rerun1\sealed_evaluation_freeze.json `
  --development-audit data\results\progressive_landmarks_v2_rerun1\development_audit.json
```

The analysis performs correctness and deterministic replay before exposing
performance data, retains all 800 planned queries, uses the ordinary even
median of eight timings per query/method, and uses 10,000 map-then-query
bootstrap replicates with seed `23725513`.

The frozen run records deliberately bind the exact original interpreter,
platform, source-tree imports, and authorization path. Consequently, deep
loading the authoritative run in a relocated clone is expected to fail closed;
the portable release audit verifies its immutable bytes, manifests, hashes,
counts, authorization, analysis self-hashes, and non-reliance on the
superseded attempt. Running the four commands above in a new environment is a
new reproduction and must publish to new output paths with new hashes rather
than impersonating the frozen evidence.

## Deterministic public release

The working directory contains private course materials and retired research
programs that must not be published. Build the public artifact only through the
explicit 152-file allowlist in
`scripts/package_progressive_landmarks_release.py`; never use `git add .` from
this mixed archive. The packager rejects links, path escapes, missing or extra
files, any payload at the 50 MiB GitHub warning boundary, nondeterministic ZIP
metadata, hash drift, and unresolved student/repository placeholders.

The pre-metadata 152-file draft boundary was built twice with identical ZIP bytes.
Its generated manifest records every member hash and its own canonical
self-hash without embedding a circular archive hash in the archive itself. In a
fresh extraction, the full public suite reported `139 passed, 3 skipped`:
the same two optional Windows symlink-privilege branches plus the
`test_repro_audit` historical-checkpoint case, whose historical checkpoint is
intentionally absent from the public artifact. The extracted protocol verifier
reproduced the exact plan SHA-256 above, syntax compilation passed, the active
audit reported `PASS=8`, `PENDING=2` (administrative only), `FAIL=0`, and
`WARN=0`, and the self-contained analysis loader passed. It also confirmed that
`aaai2027.sty` is absent: AAAI's copyright notice prohibits redistribution
without written permission, so report rebuilds retrieve and hash-check the
pinned official kit documented in `report/AAAI27_AUTHOR_KIT_PROVENANCE.md`.
That isolated validation established the release boundary before metadata
finalization.  A finalized checkout must pass the same gates without
`--allow-draft`.

For an intentionally incomplete QA checkout, the packager requires the
explicit draft override:

```powershell
& $Python scripts\package_progressive_landmarks_release.py `
  tmp\progressive-landmarks-draft.zip --allow-draft
& $Python scripts\package_progressive_landmarks_release.py `
  tmp\progressive-landmarks-draft.zip --verify --allow-draft
```

After replacing the two student name/ID lines and repository URL/commit, omit
`--allow-draft`; a default build must refuse any incomplete submission. The ZIP
is stored in sorted POSIX-path order with fixed timestamps and modes, and its
manifest binds every member's size and SHA-256 plus a canonical manifest
self-hash. The release excludes course-distributed instructions/examples,
chosen-project registers, superseded raw attempts, and legacy MVC/CBS solver,
configuration, and result artifacts.

Because a Git commit cannot contain its own SHA, publication uses this exact
two-commit sequence:

1. Derive `PAYLOAD_PATHS` from the packager, stage exactly those 152 paths
   (never `git add .`), verify the staged inventory against that allowlist, and
   create scientific commit A.
2. Configure `origin` as the actual canonical public HTTPS repository base,
   push commit A, and derive `$ScientificSha` from the unchanged local `HEAD`
   and `$RepositoryUrl` from that configured remote. The values supplied to
   the finalizer must equal those two local Git facts.
3. Independently open `$RepositoryUrl/commit/$ScientificSha` in a browser that
   is not relying on a private signed-in session and verify that commit A is
   publicly readable.
4. Run the metadata finalizer first with `--dry-run`, inspect its proposed
   three-file transaction, and only then repeat it with `--apply`:

   ```powershell
   & $Python scripts\finalize_progressive_landmarks_metadata.py `
     --repository-root . `
     --student-one-name $StudentOneName --student-one-id $StudentOneId `
     --student-two-name $StudentTwoName --student-two-id $StudentTwoId `
     --repository-url $RepositoryUrl `
     --scientific-commit-sha $ScientificSha --dry-run --json

   & $Python scripts\finalize_progressive_landmarks_metadata.py `
     --repository-root . `
     --student-one-name $StudentOneName --student-one-id $StudentOneId `
     --student-two-name $StudentTwoName --student-two-id $StudentTwoId `
     --repository-url $RepositoryUrl `
     --scientific-commit-sha $ScientificSha --apply --json
   ```

5. Rebuild and visually inspect the PDF, run the complete audit and final
   non-draft package validation, then create commit B containing only identity
   metadata, the finalizer-targeted README/report sources, rebuilt report
   artifacts, and administrative status/provenance updates. Commit B must not
   alter frozen code, configuration, inputs, raw results, or analysis.

The course-required link therefore identifies the exact experiment and
figure-generation code in immutable commit A without a circular or guessed
hash. Commit B and the final release retain the completed report.

## Repository layout

```text
active_project/                         governing proposal, specification, status, plan, provenance
configs/progressive_landmarks_v2.json   frozen machine-readable protocol
src/python/progressive_landmarks/       active implementation
scripts/*progressive_landmarks*.py      protocol, run, freeze, and analysis entry points
scripts/package_progressive_landmarks_release.py
                                        deterministic public-release allowlist
tests/python/test_progressive_*.py      unit, tamper, replay, and integration tests
data/source/movingai_mapf_2021-06-17/   checksum-bound public input snapshot
data/results/progressive_landmarks_v2_rerun1/
                                        authoritative raw runs and authorization
data/processed/progressive_landmarks_analysis_v2/
                                        authoritative analysis, tables, and figures
report/                                 final submission source and PDF
```

Root-level `PROJECT_SPEC.md`, `PLAN.md`, `STATUS.md`, `RESEARCH_LOG.md`, and
`EXPERIMENT_LOG.md`, along with the C++ sources and old result directories,
belong to the frozen MVC/CBS history. They are intentionally preserved but do
not govern or support the active progressive-landmarks claims.

## Publication status

The experiment, analysis, report, citation audit, register recheck, official
PDF build, and rendered-page inspection are complete.  The student identities
and public repository metadata are finalized.  The report links to the exact
scientific snapshot used for the experiments and figure generation:

<https://github.com/NevoLevi/progressive-landmark-astar/commit/4ee61db6787528efb7e01326e3c23d0006515570>
