# Moving AI MAPF source snapshot

This directory is reserved for a checksum-pinned research-use snapshot of the
official Moving AI MAPF benchmark files used by the proposed CBS development
corpus.

- Authoritative index: <https://www.movingai.com/benchmarks/mapf/index.html>
- Map archive: <https://www.movingai.com/benchmarks/mapf/mapf-map.zip>
- Random-scenario archive:
  <https://www.movingai.com/benchmarks/mapf/mapf-scen-random.zip>
- Snapshot label: `movingai_mapf_2021-06-17`, keyed to the last dataset change
  explicitly reported by the official index (the rebuild of
  `room-64-64-16.map` on 2021-06-17). This is a local snapshot label, not an
  upstream semantic version.

The official MAPF benchmark page states that all data is available under the
[Open Data Commons Attribution License (ODC-By) 1.0](https://opendatacommons.org/licenses/by/1-0/).
That license permits sharing, modification, and use subject to attribution and
notice requirements.  Preserve this file (including the license URI) with any
publicly conveyed snapshot and attribute the Moving AI MAPF benchmark.  A
suitable produced-work notice is: "Contains information from the Moving AI
MAPF Benchmark, which is made available under the ODC Attribution License."

The benchmark page asks papers using these data to cite:

Roni Stern, Nathan R. Sturtevant, Ariel Felner, Sven Koenig, Hang Ma, Thayne T.
Walker, Jiaoyang Li, Dor Atzmon, Liron Cohen, T. K. Satish Kumar, Eli Boyarski,
and Roman Bartak. "Multi-Agent Pathfinding: Definitions, Variants, and
Benchmarks." *SoCS 2019*, pages 151--158.
<https://doi.org/10.1609/socs.v10i1.18510>

`SHA256SUMS` pins both archives and every extracted benchmark payload after
download and structural validation. `CORPUS_MANIFEST.json` records the
prospective map-level split and has SHA-256
`c4425b97a0ed60cf389c35d7bda9a2756da89bd687431593e10e89b3757e1bb9`.
