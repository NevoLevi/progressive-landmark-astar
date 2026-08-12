"""Deterministic grid search with progressively evaluated landmark heuristics.

The module deliberately depends only on the Python standard library.  Coordinates
are always ``(x, y)`` pairs, maps use four-neighbour unit-cost movement, and all
public value objects are immutable.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import heapq
from pathlib import Path
import re
import struct
from time import perf_counter_ns
from typing import Final, Iterator, Literal, Sequence


Cell = tuple[int, int]
SearchMode = Literal["manhattan", "eager_full", "lazy_full", "staged"]

_ALLOWED_TERRAIN: Final = frozenset(".@T")
_BLOCKED_TERRAIN: Final = frozenset("@T")
_UNREACHABLE_U32: Final = (1 << 32) - 1
_NEIGHBOUR_OFFSETS: Final = ((0, -1), (-1, 0), (1, 0), (0, 1))
_MODES: Final = frozenset(("manhattan", "eager_full", "lazy_full", "staged"))
_DIGEST_PREFIX: Final = b"progressive-landmarks-expansion-v1\0"
_POSITIVE_INTEGER_RE: Final = re.compile(r"[1-9][0-9]*\Z")


class MapFormatError(ValueError):
    """Raised when a grid or Moving AI map violates the accepted format."""


class SearchInputError(ValueError):
    """Raised when a search configuration or endpoint is invalid."""


class PathValidationError(ValueError):
    """Raised when a returned or supplied path is not a legal grid path."""


def _is_plain_int(value: object) -> bool:
    return type(value) is int


def _require_cell(cell: object, *, label: str) -> Cell:
    if (
        type(cell) is not tuple
        or len(cell) != 2
        or not _is_plain_int(cell[0])
        or not _is_plain_int(cell[1])
    ):
        raise SearchInputError(f"{label} must be an (x, y) tuple of plain integers")
    return cell


@dataclass(frozen=True, slots=True)
class GridMap:
    """An immutable four-neighbour, unit-cost obstacle grid.

    ``.`` is traversable.  Both ``@`` and ``T`` are blocked, matching the
    deliberately narrow terrain subset accepted by :func:`read_moving_ai_map`.
    """

    width: int
    height: int
    rows: tuple[str, ...]
    name: str = "<memory>"

    def __post_init__(self) -> None:
        if not _is_plain_int(self.width) or self.width <= 0:
            raise MapFormatError("width must be a positive plain integer")
        if not _is_plain_int(self.height) or self.height <= 0:
            raise MapFormatError("height must be a positive plain integer")
        if type(self.rows) is not tuple or len(self.rows) != self.height:
            raise MapFormatError("rows must be an immutable tuple matching height")
        if type(self.name) is not str or not self.name:
            raise MapFormatError("name must be a non-empty string")
        for y, row in enumerate(self.rows):
            if type(row) is not str or len(row) != self.width:
                raise MapFormatError(f"row {y} does not match declared width")
            unexpected = set(row) - _ALLOWED_TERRAIN
            if unexpected:
                rendered = "".join(sorted(unexpected))
                raise MapFormatError(
                    f"row {y} contains unsupported terrain: {rendered!r}"
                )
        if self.width * self.height >= _UNREACHABLE_U32:
            raise MapFormatError("grid is too large for packed 32-bit distance tables")

    @property
    def cell_count(self) -> int:
        return self.width * self.height

    @property
    def traversable_count(self) -> int:
        return sum(row.count(".") for row in self.rows)

    def contains(self, cell: Cell) -> bool:
        x, y = _require_cell(cell, label="cell")
        return 0 <= x < self.width and 0 <= y < self.height

    def traversable(self, cell: Cell) -> bool:
        x, y = _require_cell(cell, label="cell")
        return 0 <= x < self.width and 0 <= y < self.height and self.rows[y][x] == "."

    def index(self, cell: Cell) -> int:
        x, y = _require_cell(cell, label="cell")
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise SearchInputError(f"cell {cell!r} is outside the map")
        return y * self.width + x

    def cell(self, index: int) -> Cell:
        if not _is_plain_int(index) or not 0 <= index < self.cell_count:
            raise SearchInputError("cell index is outside the map")
        return (index % self.width, index // self.width)

    def iter_traversable(self) -> Iterator[Cell]:
        for y, row in enumerate(self.rows):
            for x, terrain in enumerate(row):
                if terrain == ".":
                    yield (x, y)

    def neighbours(self, cell: Cell) -> tuple[Cell, ...]:
        x, y = _require_endpoint(self, cell, label="cell")
        result: list[Cell] = []
        for dx, dy in _NEIGHBOUR_OFFSETS:
            neighbour = (x + dx, y + dy)
            if self.traversable(neighbour):
                result.append(neighbour)
        return tuple(result)


def _require_endpoint(grid: GridMap, cell: object, *, label: str) -> Cell:
    checked = _require_cell(cell, label=label)
    if not grid.contains(checked):
        raise SearchInputError(f"{label} {checked!r} is outside the map")
    if not grid.traversable(checked):
        raise SearchInputError(f"{label} {checked!r} is blocked")
    return checked


def read_moving_ai_map(path: str | Path) -> GridMap:
    """Read a strict Moving AI ``.map`` file.

    Accepted files contain exactly ``type octile``, canonical positive height and
    width headers, ``map``, and the declared number of rows.  Terrain is limited
    to ``.``, ``@``, and ``T``.  ASCII, LF/CRLF line endings, and at most one
    terminal newline are accepted; BOMs, blank/extra lines, and loose headers are
    rejected.
    """

    map_path = Path(path)
    try:
        payload = map_path.read_bytes()
    except OSError as error:
        raise MapFormatError(f"cannot read map {map_path}: {error}") from error
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise MapFormatError("Moving AI maps must be ASCII without a BOM") from error
    if "\r" in text.replace("\r\n", ""):
        raise MapFormatError("map contains a bare carriage return")
    text = text.replace("\r\n", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    lines = text.split("\n")
    if len(lines) < 4:
        raise MapFormatError("map is missing required headers")
    if lines[0] != "type octile":
        raise MapFormatError("first header must be exactly 'type octile'")

    def parse_extent(line: str, key: str) -> int:
        prefix = f"{key} "
        if not line.startswith(prefix):
            raise MapFormatError(f"expected exact '{key} N' header")
        raw = line[len(prefix) :]
        if _POSITIVE_INTEGER_RE.fullmatch(raw) is None:
            raise MapFormatError(f"{key} must be a canonical positive integer")
        return int(raw)

    height = parse_extent(lines[1], "height")
    width = parse_extent(lines[2], "width")
    if lines[3] != "map":
        raise MapFormatError("fourth header must be exactly 'map'")
    if len(lines) != height + 4:
        raise MapFormatError("number of map rows does not match declared height")
    return GridMap(width, height, tuple(lines[4:]), map_path.name)


@dataclass(frozen=True, slots=True)
class ComponentIndex:
    """Immutable connected-component labels in row-major cell order."""

    grid: GridMap
    labels: tuple[int, ...]
    count: int

    def __post_init__(self) -> None:
        if type(self.labels) is not tuple or len(self.labels) != self.grid.cell_count:
            raise ValueError("component labels must match the grid")
        if not _is_plain_int(self.count) or self.count < 0:
            raise ValueError("component count must be a non-negative integer")
        valid_labels = set(range(self.count))
        for index, label in enumerate(self.labels):
            if not _is_plain_int(label):
                raise ValueError("component labels must be plain integers")
            cell = self.grid.cell(index)
            expected_domain = valid_labels if self.grid.traversable(cell) else {-1}
            if label not in expected_domain:
                raise ValueError("component labels disagree with grid terrain")

    def component_of(self, cell: Cell) -> int | None:
        checked = _require_cell(cell, label="cell")
        if not self.grid.contains(checked) or not self.grid.traversable(checked):
            return None
        return self.labels[self.grid.index(checked)]

    def connected(self, first: Cell, second: Cell) -> bool:
        first_component = self.component_of(first)
        return first_component is not None and first_component == self.component_of(
            second
        )


def connected_components(grid: GridMap) -> ComponentIndex:
    labels = [-1] * grid.cell_count
    component_count = 0
    for source in grid.iter_traversable():
        source_index = grid.index(source)
        if labels[source_index] != -1:
            continue
        labels[source_index] = component_count
        queue: deque[Cell] = deque((source,))
        while queue:
            cell = queue.popleft()
            for neighbour in grid.neighbours(cell):
                neighbour_index = grid.index(neighbour)
                if labels[neighbour_index] == -1:
                    labels[neighbour_index] = component_count
                    queue.append(neighbour)
        component_count += 1
    return ComponentIndex(grid, tuple(labels), component_count)


def bfs_distances(grid: GridMap, source: Cell) -> tuple[int, ...]:
    """Return exact unit-cost distances, with ``-1`` for blocked/unreachable cells."""

    checked_source = _require_endpoint(grid, source, label="source")
    distances = [-1] * grid.cell_count
    distances[grid.index(checked_source)] = 0
    queue: deque[Cell] = deque((checked_source,))
    while queue:
        cell = queue.popleft()
        next_distance = distances[grid.index(cell)] + 1
        for neighbour in grid.neighbours(cell):
            neighbour_index = grid.index(neighbour)
            if distances[neighbour_index] == -1:
                distances[neighbour_index] = next_distance
                queue.append(neighbour)
    return tuple(distances)


def bfs_distance(grid: GridMap, start: Cell, goal: Cell) -> int | None:
    checked_goal = _require_endpoint(grid, goal, label="goal")
    distance = bfs_distances(grid, start)[grid.index(checked_goal)]
    return None if distance < 0 else distance


@dataclass(frozen=True, slots=True)
class BFSResult:
    found: bool
    cost: int | None
    path: tuple[Cell, ...]


def bfs_shortest_path(grid: GridMap, start: Cell, goal: Cell) -> BFSResult:
    checked_start = _require_endpoint(grid, start, label="start")
    checked_goal = _require_endpoint(grid, goal, label="goal")
    parents: dict[Cell, Cell | None] = {checked_start: None}
    queue: deque[Cell] = deque((checked_start,))
    while queue:
        cell = queue.popleft()
        if cell == checked_goal:
            path = _reconstruct_path(parents, checked_goal, grid.cell_count)
            validate_path(grid, path, checked_start, checked_goal, len(path) - 1)
            return BFSResult(True, len(path) - 1, path)
        for neighbour in grid.neighbours(cell):
            if neighbour not in parents:
                parents[neighbour] = cell
                queue.append(neighbour)
    return BFSResult(False, None, ())


def _select_farthest_first_landmarks_with_distances(
    grid: GridMap, landmark_count: int = 32
) -> tuple[tuple[Cell, ...], tuple[tuple[int, ...], ...]]:
    """Select landmarks and retain each selection BFS for table construction."""

    if not _is_plain_int(landmark_count) or landmark_count <= 0:
        raise ValueError("landmark_count must be a positive plain integer")
    candidates = tuple(grid.iter_traversable())
    target = min(landmark_count, len(candidates))
    if target == 0:
        return (), ()
    selected: list[Cell] = []
    selected_indices: set[int] = set()
    distance_rows: list[tuple[int, ...]] = []
    minimum_distances = [_UNREACHABLE_U32] * grid.cell_count
    for _ in range(target):
        if not selected:
            landmark = candidates[0]
        else:
            landmark = max(
                (
                    cell
                    for cell in candidates
                    if grid.index(cell) not in selected_indices
                ),
                key=lambda cell: (
                    minimum_distances[grid.index(cell)],
                    -grid.index(cell),
                ),
            )
        landmark_index = grid.index(landmark)
        selected.append(landmark)
        selected_indices.add(landmark_index)
        distances = bfs_distances(grid, landmark)
        distance_rows.append(distances)
        for cell in candidates:
            cell_index = grid.index(cell)
            distance = distances[cell_index]
            if distance >= 0 and distance < minimum_distances[cell_index]:
                minimum_distances[cell_index] = distance
    return tuple(selected), tuple(distance_rows)


def select_farthest_first_landmarks(
    grid: GridMap, landmark_count: int = 32
) -> tuple[Cell, ...]:
    """Select deterministic map-level farthest-first landmarks.

    The first landmark and all distance ties use row-major order.  Unrepresented
    connected components have infinite priority, so one point is selected from
    every component (subject to the requested budget) before a component receives
    a second point.  No query endpoints participate in selection.
    """

    landmarks, _ = _select_farthest_first_landmarks_with_distances(grid, landmark_count)
    return landmarks


@dataclass(frozen=True, slots=True)
class LandmarkTable:
    """Immutable little-endian uint32 distance rows, one per landmark."""

    grid: GridMap
    landmarks: tuple[Cell, ...]
    packed_distances: tuple[bytes, ...]

    def __post_init__(self) -> None:
        if (
            type(self.landmarks) is not tuple
            or type(self.packed_distances) is not tuple
        ):
            raise ValueError("landmarks and packed distances must be tuples")
        if len(self.landmarks) != len(self.packed_distances):
            raise ValueError("each landmark must have exactly one packed distance row")
        if len(set(self.landmarks)) != len(self.landmarks):
            raise ValueError("landmarks must be distinct")
        expected_bytes = self.grid.cell_count * 4
        for pivot, packed in zip(self.landmarks, self.packed_distances, strict=True):
            _require_endpoint(self.grid, pivot, label="landmark")
            if type(packed) is not bytes or len(packed) != expected_bytes:
                raise ValueError(
                    "packed distance row has the wrong immutable representation"
                )
            if self.distance(self.landmarks.index(pivot), pivot) != 0:
                raise ValueError("a landmark must have distance zero from itself")

    def __len__(self) -> int:
        return len(self.landmarks)

    def distance(self, landmark_index: int, cell: Cell) -> int | None:
        if not _is_plain_int(landmark_index) or not 0 <= landmark_index < len(
            self.landmarks
        ):
            raise IndexError("landmark index is out of range")
        checked = _require_cell(cell, label="cell")
        if not self.grid.contains(checked):
            raise SearchInputError(f"cell {checked!r} is outside the map")
        offset = self.grid.index(checked) * 4
        value = struct.unpack_from("<I", self.packed_distances[landmark_index], offset)[
            0
        ]
        return None if value == _UNREACHABLE_U32 else value


def build_landmark_table(grid: GridMap, landmark_count: int = 32) -> LandmarkTable:
    landmarks, distance_rows = _select_farthest_first_landmarks_with_distances(
        grid, landmark_count
    )
    packed_rows: list[bytes] = []
    for distances in distance_rows:
        packed = bytearray(grid.cell_count * 4)
        for index, distance in enumerate(distances):
            value = _UNREACHABLE_U32 if distance < 0 else distance
            struct.pack_into("<I", packed, index * 4, value)
        packed_rows.append(bytes(packed))
    return LandmarkTable(grid, landmarks, tuple(packed_rows))


def manhattan_distance(first: Cell, second: Cell) -> int:
    first_x, first_y = _require_cell(first, label="first")
    second_x, second_y = _require_cell(second, label="second")
    return abs(first_x - second_x) + abs(first_y - second_y)


def _landmark_range_bound(
    table: LandmarkTable,
    state: Cell,
    goal: Cell,
    start_index: int,
    stop_index: int,
    base: int,
) -> int:
    estimate = base
    for pivot_index in range(start_index, stop_index):
        state_distance = table.distance(pivot_index, state)
        goal_distance = table.distance(pivot_index, goal)
        if state_distance is not None and goal_distance is not None:
            estimate = max(estimate, abs(state_distance - goal_distance))
    return estimate


def _effective_landmark_counts(
    table: LandmarkTable, prefix_landmarks: int, full_landmarks: int
) -> tuple[int, int]:
    if not _is_plain_int(prefix_landmarks) or prefix_landmarks < 0:
        raise SearchInputError("prefix_landmarks must be a non-negative plain integer")
    if not _is_plain_int(full_landmarks) or full_landmarks <= 0:
        raise SearchInputError("full_landmarks must be a positive plain integer")
    if prefix_landmarks > full_landmarks:
        raise SearchInputError("prefix_landmarks cannot exceed full_landmarks")
    required = min(full_landmarks, table.grid.traversable_count)
    if len(table) < required:
        raise SearchInputError(
            f"landmark table has {len(table)} rows but {required} are required"
        )
    effective_full = min(full_landmarks, len(table))
    return min(prefix_landmarks, effective_full), effective_full


@dataclass(frozen=True, slots=True)
class NestedEstimate:
    manhattan: int
    prefix: int
    full: int


def nested_differential_estimates(
    table: LandmarkTable,
    state: Cell,
    goal: Cell,
    *,
    prefix_landmarks: int = 4,
    full_landmarks: int = 32,
) -> NestedEstimate:
    """Return nested ``Manhattan <= prefix <= full`` heuristic estimates."""

    checked_state = _require_endpoint(table.grid, state, label="state")
    checked_goal = _require_endpoint(table.grid, goal, label="goal")
    prefix_count, full_count = _effective_landmark_counts(
        table, prefix_landmarks, full_landmarks
    )
    manhattan = manhattan_distance(checked_state, checked_goal)
    prefix = _landmark_range_bound(
        table, checked_state, checked_goal, 0, prefix_count, manhattan
    )
    full = _landmark_range_bound(
        table, checked_state, checked_goal, prefix_count, full_count, prefix
    )
    return NestedEstimate(manhattan, prefix, full)


@dataclass(frozen=True, slots=True)
class SearchStats:
    """Search measurements; every field except the two nanosecond fields is deterministic."""

    expanded: int
    generated: int
    relaxations: int
    reopened: int
    pops: int
    stale_pops: int
    requeues: int
    manhattan_calls: int
    prefix_calls: int
    suffix_calls: int
    full_calls: int
    pivot_evaluations: int
    distance_table_reads: int
    heuristic_cache_hits: int
    unique_discovered: int
    max_open_entries: int
    max_live_states: int
    stage_ns: int
    search_ns: int


@dataclass(frozen=True, slots=True)
class SearchResult:
    mode: SearchMode
    found: bool
    cost: int | None
    path: tuple[Cell, ...]
    expansion_digest: str
    stats: SearchStats


@dataclass(slots=True)
class _MutableStats:
    expanded: int = 0
    generated: int = 0
    relaxations: int = 0
    reopened: int = 0
    pops: int = 0
    stale_pops: int = 0
    requeues: int = 0
    manhattan_calls: int = 0
    prefix_calls: int = 0
    suffix_calls: int = 0
    full_calls: int = 0
    pivot_evaluations: int = 0
    distance_table_reads: int = 0
    heuristic_cache_hits: int = 0
    unique_discovered: int = 0
    max_open_entries: int = 0
    max_live_states: int = 0
    stage_ns: int = 0

    def freeze(self, search_ns: int) -> SearchStats:
        return SearchStats(
            self.expanded,
            self.generated,
            self.relaxations,
            self.reopened,
            self.pops,
            self.stale_pops,
            self.requeues,
            self.manhattan_calls,
            self.prefix_calls,
            self.suffix_calls,
            self.full_calls,
            self.pivot_evaluations,
            self.distance_table_reads,
            self.heuristic_cache_hits,
            self.unique_discovered,
            self.max_open_entries,
            self.max_live_states,
            self.stage_ns,
            search_ns,
        )


@dataclass(slots=True)
class _Label:
    g: int
    parent: Cell | None
    version: int
    ordinal: int
    stage: int
    h: int


@dataclass(slots=True)
class _HeuristicCache:
    """Strongest query-specific heuristic stage already known for one state."""

    stage: int
    h: int


def _search_landmark_bound(
    table: LandmarkTable,
    state_index: int,
    goal_distances: tuple[int | None, ...],
    start_index: int,
    stop_index: int,
    base: int,
    stats: _MutableStats,
    call_kind: Literal["prefix", "suffix", "full"],
    measure_stage_time: bool,
) -> int:
    """Evaluate a pivot range with one raw state-table read per pivot.

    Public ``LandmarkTable.distance`` remains the checked inspection API.  This
    search-only hot path validates the state once, caches all goal distances once
    per query, and then reads packed rows directly.  That keeps the experiment
    about landmark work rather than repeated Python argument validation.
    """

    started = perf_counter_ns() if measure_stage_time else 0
    estimate = base
    offset = state_index * 4
    for pivot_index in range(start_index, stop_index):
        state_raw = struct.unpack_from(
            "<I", table.packed_distances[pivot_index], offset
        )[0]
        goal_distance = goal_distances[pivot_index]
        if state_raw != _UNREACHABLE_U32 and goal_distance is not None:
            estimate = max(estimate, abs(state_raw - goal_distance))
    if measure_stage_time:
        stats.stage_ns += perf_counter_ns() - started
    if call_kind == "prefix":
        stats.prefix_calls += 1
    elif call_kind == "suffix":
        stats.suffix_calls += 1
    else:
        stats.full_calls += 1
    evaluated = stop_index - start_index
    stats.pivot_evaluations += evaluated
    stats.distance_table_reads += evaluated
    return estimate


def _expansion_digest_update(digest: object, cell: Cell) -> None:
    # GridMap bounds guarantee both coordinates fit in unsigned 32 bits.
    digest.update(struct.pack("<II", cell[0], cell[1]))


def _reconstruct_path(
    parents: dict[Cell, Cell | None], goal: Cell, maximum_cells: int
) -> tuple[Cell, ...]:
    reversed_path: list[Cell] = []
    current: Cell | None = goal
    while current is not None:
        reversed_path.append(current)
        if len(reversed_path) > maximum_cells + 1:
            raise PathValidationError("parent relation contains a cycle")
        current = parents[current]
    reversed_path.reverse()
    return tuple(reversed_path)


def validate_path(
    grid: GridMap,
    path: Sequence[Cell],
    start: Cell,
    goal: Cell,
    expected_cost: int,
) -> None:
    """Fail closed unless ``path`` is an exact legal unit-cost start-to-goal path."""

    checked_start = _require_endpoint(grid, start, label="start")
    checked_goal = _require_endpoint(grid, goal, label="goal")
    if type(path) is not tuple or not path:
        raise PathValidationError("path must be a non-empty immutable tuple")
    if not _is_plain_int(expected_cost) or expected_cost < 0:
        raise PathValidationError("expected_cost must be a non-negative plain integer")
    checked_path: list[Cell] = []
    for offset, cell in enumerate(path):
        try:
            checked_path.append(_require_endpoint(grid, cell, label=f"path[{offset}]"))
        except SearchInputError as error:
            raise PathValidationError(str(error)) from error
    if checked_path[0] != checked_start or checked_path[-1] != checked_goal:
        raise PathValidationError("path endpoints do not match the requested query")
    if len(checked_path) - 1 != expected_cost:
        raise PathValidationError("path length does not match the declared unit cost")
    for first, second in zip(checked_path, checked_path[1:]):
        if manhattan_distance(first, second) != 1:
            raise PathValidationError("path contains a non-cardinal or non-unit move")


def astar_search(
    grid: GridMap,
    start: Cell,
    goal: Cell,
    *,
    mode: SearchMode,
    landmarks: LandmarkTable | None = None,
    prefix_landmarks: int = 4,
    full_landmarks: int = 32,
    measure_stage_time: bool = False,
) -> SearchResult:
    """Run deterministic optimal A* in one of four heuristic-evaluation modes.

    OPEN is ordered by ``(f, -g, ordinal, row-major state)``.  A successful
    relaxation receives a fresh ordinal, while lazy heuristic promotions preserve
    it.  Following published Lazy A*, landmark modes fully evaluate Start before
    its first insertion, while a valid popped Goal terminates before refinement.
    Per-state heuristic values survive strict ``g`` improvements because they are
    independent of the path used to reach the state.
    """

    if type(mode) is not str or mode not in _MODES:
        raise SearchInputError(f"unsupported search mode: {mode!r}")
    checked_start = _require_endpoint(grid, start, label="start")
    checked_goal = _require_endpoint(grid, goal, label="goal")
    if not _is_plain_int(prefix_landmarks) or prefix_landmarks < 0:
        raise SearchInputError("prefix_landmarks must be a non-negative plain integer")
    if not _is_plain_int(full_landmarks) or full_landmarks <= 0:
        raise SearchInputError("full_landmarks must be a positive plain integer")
    if prefix_landmarks > full_landmarks:
        raise SearchInputError("prefix_landmarks cannot exceed full_landmarks")
    if type(measure_stage_time) is not bool:
        raise SearchInputError("measure_stage_time must be a boolean")

    if mode == "manhattan":
        prefix_count = 0
        full_count = 0
    else:
        if landmarks is None:
            raise SearchInputError(f"mode {mode!r} requires a landmark table")
        if landmarks.grid != grid:
            raise SearchInputError("landmark table was built for a different map")
        prefix_count, full_count = _effective_landmark_counts(
            landmarks, prefix_landmarks, full_landmarks
        )

    final_stage = 0 if mode in ("manhattan", "eager_full") else 1
    if mode == "staged":
        final_stage = 2

    stats = _MutableStats()
    search_started = perf_counter_ns()
    goal_distances: tuple[int | None, ...] = ()
    if landmarks is not None:
        goal_offset = grid.index(checked_goal) * 4
        goal_values: list[int | None] = []
        for packed in landmarks.packed_distances[:full_count]:
            raw = struct.unpack_from("<I", packed, goal_offset)[0]
            goal_values.append(None if raw == _UNREACHABLE_U32 else raw)
        goal_distances = tuple(goal_values)
        stats.distance_table_reads += full_count

    digest = hashlib.sha256(_DIGEST_PREFIX)
    labels: dict[Cell, _Label] = {}
    heuristic_cache: dict[Cell, _HeuristicCache] = {}
    parents: dict[Cell, Cell | None] = {}
    closed: set[Cell] = set()
    live: set[Cell] = set()
    # (f, -g, ordinal, row-major index, version, stage)
    open_heap: list[tuple[int, int, int, int, int, int]] = []
    next_ordinal = 0
    next_version = 0

    def evaluate_range(
        cell: Cell,
        cache: _HeuristicCache,
        start_index: int,
        stop_index: int,
        call_kind: Literal["prefix", "suffix", "full"],
    ) -> None:
        assert landmarks is not None
        cache.h = _search_landmark_bound(
            landmarks,
            grid.index(cell),
            goal_distances,
            start_index,
            stop_index,
            cache.h,
            stats,
            call_kind,
            measure_stage_time,
        )

    def initial_heuristic(cell: Cell) -> _HeuristicCache:
        stats.manhattan_calls += 1
        cache = _HeuristicCache(0, manhattan_distance(cell, checked_goal))
        heuristic_cache[cell] = cache
        stats.unique_discovered += 1
        if mode == "eager_full":
            evaluate_range(cell, cache, 0, full_count, "full")
        return cache

    def cached_or_initial_heuristic(cell: Cell) -> _HeuristicCache:
        cache = heuristic_cache.get(cell)
        if cache is not None:
            stats.heuristic_cache_hits += 1
            return cache
        return initial_heuristic(cell)

    def push(cell: Cell, label: _Label) -> None:
        heapq.heappush(
            open_heap,
            (
                label.g + label.h,
                -label.g,
                label.ordinal,
                grid.index(cell),
                label.version,
                label.stage,
            ),
        )
        stats.max_open_entries = max(stats.max_open_entries, len(open_heap))

    start_cache = initial_heuristic(checked_start)
    # Lazy A* initializes Start with the strongest heuristic.  Progressive
    # evaluation performs the two nested ranges here without artificial OPEN
    # cycles, while still recording their exact work separately.
    if mode == "lazy_full":
        evaluate_range(checked_start, start_cache, 0, full_count, "full")
        start_cache.stage = 1
    elif mode == "staged":
        evaluate_range(checked_start, start_cache, 0, prefix_count, "prefix")
        start_cache.stage = 1
        evaluate_range(checked_start, start_cache, prefix_count, full_count, "suffix")
        start_cache.stage = 2

    start_label = _Label(
        0,
        None,
        next_version,
        next_ordinal,
        start_cache.stage,
        start_cache.h,
    )
    labels[checked_start] = start_label
    parents[checked_start] = None
    live.add(checked_start)
    push(checked_start, start_label)
    stats.max_live_states = 1
    next_ordinal += 1
    next_version += 1

    while open_heap:
        _, negative_g, ordinal, cell_index, version, stage = heapq.heappop(open_heap)
        stats.pops += 1
        cell = grid.cell(cell_index)
        label = labels.get(cell)
        if (
            label is None
            or cell not in live
            or label.version != version
            or label.stage != stage
            or label.g != -negative_g
            or label.ordinal != ordinal
        ):
            stats.stale_pops += 1
            continue

        # Every admissible heuristic is zero at Goal.  Testing it before lazy
        # refinement is both optimal and the standard Lazy-A* contract.
        if cell == checked_goal:
            path = _reconstruct_path(parents, checked_goal, grid.cell_count)
            validate_path(grid, path, checked_start, checked_goal, label.g)
            elapsed = perf_counter_ns() - search_started
            return SearchResult(
                mode,
                True,
                label.g,
                path,
                digest.hexdigest(),
                stats.freeze(elapsed),
            )

        if stage < final_stage:
            cache = heuristic_cache[cell]
            if cache.stage != stage or cache.h != label.h:
                raise AssertionError("label and heuristic cache diverged")
            if mode == "lazy_full":
                evaluate_range(cell, cache, 0, full_count, "full")
            elif stage == 0:
                evaluate_range(cell, cache, 0, prefix_count, "prefix")
            else:
                evaluate_range(cell, cache, prefix_count, full_count, "suffix")
            cache.stage += 1
            label.stage = cache.stage
            label.h = cache.h
            stats.requeues += 1
            push(cell, label)
            continue

        live.remove(cell)
        closed.add(cell)
        stats.expanded += 1
        _expansion_digest_update(digest, cell)

        for neighbour in grid.neighbours(cell):
            stats.generated += 1
            tentative_g = label.g + 1
            previous = labels.get(neighbour)
            if previous is not None and tentative_g >= previous.g:
                continue
            if neighbour in closed:
                closed.remove(neighbour)
                stats.reopened += 1
            cache = cached_or_initial_heuristic(neighbour)
            neighbour_label = _Label(
                tentative_g,
                cell,
                next_version,
                next_ordinal,
                cache.stage,
                cache.h,
            )
            next_version += 1
            next_ordinal += 1
            labels[neighbour] = neighbour_label
            parents[neighbour] = cell
            live.add(neighbour)
            stats.relaxations += 1
            stats.max_live_states = max(stats.max_live_states, len(live))
            push(neighbour, neighbour_label)

    elapsed = perf_counter_ns() - search_started
    return SearchResult(
        mode, False, None, (), digest.hexdigest(), stats.freeze(elapsed)
    )


__all__ = [
    "BFSResult",
    "Cell",
    "ComponentIndex",
    "GridMap",
    "LandmarkTable",
    "MapFormatError",
    "NestedEstimate",
    "PathValidationError",
    "SearchInputError",
    "SearchMode",
    "SearchResult",
    "SearchStats",
    "astar_search",
    "bfs_distance",
    "bfs_distances",
    "bfs_shortest_path",
    "build_landmark_table",
    "connected_components",
    "manhattan_distance",
    "nested_differential_estimates",
    "read_moving_ai_map",
    "select_farthest_first_landmarks",
    "validate_path",
]
