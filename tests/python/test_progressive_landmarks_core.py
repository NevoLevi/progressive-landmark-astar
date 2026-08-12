"""Exhaustive correctness tests for the progressive-landmark search core."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from progressive_landmarks import (
    GridMap,
    LandmarkTable,
    MapFormatError,
    PathValidationError,
    SearchInputError,
    astar_search,
    bfs_distance,
    bfs_distances,
    bfs_shortest_path,
    build_landmark_table,
    connected_components,
    nested_differential_estimates,
    read_moving_ai_map,
    select_farthest_first_landmarks,
    validate_path,
)


def _grid(*rows: str, name: str = "fixture.map") -> GridMap:
    return GridMap(len(rows[0]), len(rows), tuple(rows), name)


def _deterministic_stats(result: object) -> object:
    return replace(result.stats, stage_ns=0, search_ns=0)


class GridAndParserTests(unittest.TestCase):
    def test_grid_is_strict_immutable_and_uses_only_dot_at_tree(self) -> None:
        grid = _grid(".@", "T.")
        self.assertEqual(tuple(grid.iter_traversable()), ((0, 0), (1, 1)))
        self.assertEqual(grid.neighbours((0, 0)), ())
        with self.assertRaises(FrozenInstanceError):
            grid.width = 7  # type: ignore[misc]
        invalid_arguments = (
            (True, 1, (".",)),
            (1, 0, ()),
            (2, 1, (".",)),
            (1, 1, ["."]),
            (1, 1, ("G",)),
            (1, 1, (" ",)),
        )
        for width, height, rows in invalid_arguments:
            with self.subTest(width=width, height=height, rows=rows):
                with self.assertRaises(MapFormatError):
                    GridMap(width, height, rows)  # type: ignore[arg-type]

    def test_strict_moving_ai_parser_accepts_lf_and_crlf(self) -> None:
        payloads = (
            b"type octile\nheight 2\nwidth 3\nmap\n.@.\nT..\n",
            b"type octile\r\nheight 2\r\nwidth 3\r\nmap\r\n.@.\r\nT..",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.map"
            for payload in payloads:
                with self.subTest(payload=payload):
                    path.write_bytes(payload)
                    grid = read_moving_ai_map(path)
                    self.assertEqual((grid.width, grid.height), (3, 2))
                    self.assertEqual(grid.rows, (".@.", "T.."))
                    self.assertEqual(grid.name, "fixture.map")

    def test_strict_moving_ai_parser_rejects_malformed_files(self) -> None:
        invalid_payloads = (
            b"type Octile\nheight 1\nwidth 1\nmap\n.",
            b"type octile \nheight 1\nwidth 1\nmap\n.",
            b"type octile\nheight 01\nwidth 1\nmap\n.",
            b"type octile\nheight 1\nwidth 0\nmap\n.",
            b"type octile\nwidth 1\nheight 1\nmap\n.",
            b"type octile\nheight 1\nwidth 1\nMAP\n.",
            b"type octile\nheight 1\nwidth 2\nmap\n.",
            b"type octile\nheight 1\nwidth 1\nmap\nG",
            b"type octile\nheight 1\nwidth 1\nmap\n.\nextra",
            b"type octile\nheight 1\nwidth 1\nmap\n.\n\n",
            b"type octile\rheight 1\rwidth 1\rmap\r.",
            b"\xef\xbb\xbftype octile\nheight 1\nwidth 1\nmap\n.",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.map"
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    path.write_bytes(payload)
                    with self.assertRaises(MapFormatError):
                        read_moving_ai_map(path)


class OracleAndLandmarkTests(unittest.TestCase):
    def test_components_and_bfs_are_exact_on_disconnected_map(self) -> None:
        grid = _grid("..@..", ".@@@.", "..T..")
        components = connected_components(grid)
        self.assertEqual(components.count, 2)
        self.assertTrue(components.connected((0, 0), (1, 2)))
        self.assertFalse(components.connected((0, 0), (4, 2)))
        self.assertIsNone(components.component_of((2, 0)))
        self.assertEqual(bfs_distance(grid, (0, 0), (1, 2)), 3)
        self.assertIsNone(bfs_distance(grid, (0, 0), (4, 2)))
        oracle = bfs_shortest_path(grid, (0, 0), (1, 2))
        self.assertTrue(oracle.found)
        self.assertEqual(oracle.cost, 3)
        validate_path(grid, oracle.path, (0, 0), (1, 2), 3)
        self.assertEqual(
            bfs_shortest_path(grid, (0, 0), (4, 2)).path,
            (),
        )

    def test_farthest_first_selection_is_map_level_and_deterministic(self) -> None:
        connected = _grid("....", ".@@.", "....")
        first = select_farthest_first_landmarks(connected, 6)
        second = select_farthest_first_landmarks(connected, 6)
        self.assertEqual(first, second)
        self.assertEqual(first[:2], ((0, 0), (3, 2)))
        self.assertEqual(len(first), len(set(first)))

        disconnected = _grid("..@..")
        # Infinite inter-component distance takes priority after the first point.
        self.assertEqual(
            select_farthest_first_landmarks(disconnected, 3),
            ((0, 0), (3, 0), (1, 0)),
        )

    def test_packed_distance_table_is_immutable_and_matches_bfs(self) -> None:
        grid = _grid("....", ".@@.", "....")
        table = build_landmark_table(grid, 6)
        self.assertIsInstance(table.packed_distances, tuple)
        self.assertTrue(all(type(row) is bytes for row in table.packed_distances))
        with self.assertRaises(FrozenInstanceError):
            table.landmarks = ()  # type: ignore[misc]
        for pivot_index, pivot in enumerate(table.landmarks):
            distances = bfs_distances(grid, pivot)
            for cell_index in range(grid.cell_count):
                cell = grid.cell(cell_index)
                packed_distance = table.distance(pivot_index, cell)
                expected = distances[cell_index]
                self.assertEqual(packed_distance, None if expected < 0 else expected)

    def test_table_build_reuses_each_selection_bfs_exactly_once(self) -> None:
        grid = _grid("....", ".@@.", "....")
        with patch(
            "progressive_landmarks.core.bfs_distances", wraps=bfs_distances
        ) as counted_bfs:
            table = build_landmark_table(grid, 6)

        self.assertEqual(len(table), 6)
        self.assertEqual(counted_bfs.call_count, len(table))
        self.assertEqual(
            [call.args[1] for call in counted_bfs.call_args_list],
            list(table.landmarks),
        )
        self.assertEqual(select_farthest_first_landmarks(grid, 6), table.landmarks)

    def test_nested_estimates_are_admissible_consistent_and_monotone(self) -> None:
        grids = (
            _grid("....", ".@@.", "...."),
            _grid(".....", ".@.@.", "...@.", ".@..."),
        )
        for grid in grids:
            table = build_landmark_table(grid, 7)
            cells = tuple(grid.iter_traversable())
            for goal in cells:
                estimates = {
                    state: nested_differential_estimates(
                        table,
                        state,
                        goal,
                        prefix_landmarks=2,
                        full_landmarks=7,
                    )
                    for state in cells
                }
                for state in cells:
                    exact = bfs_distance(grid, state, goal)
                    if exact is None:
                        continue
                    estimate = estimates[state]
                    self.assertLessEqual(
                        estimate.manhattan, estimate.prefix, (grid.name, state, goal)
                    )
                    self.assertLessEqual(
                        estimate.prefix, estimate.full, (grid.name, state, goal)
                    )
                    self.assertLessEqual(estimate.full, exact, (grid.name, state, goal))
                    for neighbour in grid.neighbours(state):
                        if bfs_distance(grid, neighbour, goal) is None:
                            continue
                        neighbour_estimate = estimates[neighbour]
                        self.assertLessEqual(
                            estimate.manhattan, neighbour_estimate.manhattan + 1
                        )
                        self.assertLessEqual(
                            estimate.prefix, neighbour_estimate.prefix + 1
                        )
                        self.assertLessEqual(estimate.full, neighbour_estimate.full + 1)
                goal_estimate = estimates[goal]
                self.assertEqual(
                    (goal_estimate.manhattan, goal_estimate.prefix, goal_estimate.full),
                    (0, 0, 0),
                )


class SearchTests(unittest.TestCase):
    def test_all_modes_match_bfs_on_every_connected_pair(self) -> None:
        grids = (
            _grid("...", "..."),
            _grid("....", ".@@.", "...."),
            _grid("..@..", ".@@@.", "..T.."),
            _grid(".....", ".@.@.", "...@.", ".@..."),
        )
        modes = ("manhattan", "eager_full", "lazy_full", "staged")
        for grid in grids:
            table = build_landmark_table(grid, 6)
            cells = tuple(grid.iter_traversable())
            for start in cells:
                for goal in cells:
                    oracle_cost = bfs_distance(grid, start, goal)
                    for mode in modes:
                        with self.subTest(
                            grid=grid.rows, start=start, goal=goal, mode=mode
                        ):
                            result = astar_search(
                                grid,
                                start,
                                goal,
                                mode=mode,
                                landmarks=None if mode == "manhattan" else table,
                                prefix_landmarks=2,
                                full_landmarks=6,
                            )
                            self.assertEqual(result.found, oracle_cost is not None)
                            self.assertEqual(result.cost, oracle_cost)
                            if oracle_cost is None:
                                self.assertEqual(result.path, ())
                            else:
                                validate_path(
                                    grid, result.path, start, goal, oracle_cost
                                )
                            self.assertEqual(result.stats.reopened, 0)

    def test_fixed_ties_give_identical_full_heuristic_expansion_digest(self) -> None:
        grids = (
            _grid(".......", ".@@.@..", "...@...", "..@....", "......."),
            _grid("....", ".@@.", "...."),
        )
        for grid in grids:
            table = build_landmark_table(grid, 8)
            cells = tuple(grid.iter_traversable())
            for start in cells:
                for goal in cells:
                    if bfs_distance(grid, start, goal) is None:
                        continue
                    results = {
                        mode: astar_search(
                            grid,
                            start,
                            goal,
                            mode=mode,
                            landmarks=table,
                            prefix_landmarks=3,
                            full_landmarks=8,
                        )
                        for mode in ("eager_full", "lazy_full", "staged")
                    }
                    digests = {result.expansion_digest for result in results.values()}
                    self.assertEqual(
                        len(digests),
                        1,
                        (grid.rows, start, goal, results),
                    )
                    self.assertEqual(
                        len({result.stats.expanded for result in results.values()}), 1
                    )
                    self.assertEqual(
                        len({result.stats.generated for result in results.values()}), 1
                    )

    def test_results_and_all_non_timing_counters_are_deterministic(self) -> None:
        grid = _grid(".......", ".@@.@..", "...@...", "..@....", ".......")
        table = build_landmark_table(grid, 8)
        for mode in ("manhattan", "eager_full", "lazy_full", "staged"):
            kwargs = {
                "mode": mode,
                "landmarks": None if mode == "manhattan" else table,
                "prefix_landmarks": 3,
                "full_landmarks": 8,
            }
            first = astar_search(grid, (0, 0), (6, 4), **kwargs)
            second = astar_search(grid, (0, 0), (6, 4), **kwargs)
            self.assertEqual(first.path, second.path)
            self.assertEqual(first.expansion_digest, second.expansion_digest)
            self.assertEqual(_deterministic_stats(first), _deterministic_stats(second))

    def test_stage_counters_describe_exact_work(self) -> None:
        grid = _grid(".......", ".@@.@..", "...@...", "..@....", ".......")
        table = build_landmark_table(grid, 8)
        eager = astar_search(
            grid,
            (0, 0),
            (6, 4),
            mode="eager_full",
            landmarks=table,
            prefix_landmarks=3,
            full_landmarks=8,
        )
        lazy = astar_search(
            grid,
            (0, 0),
            (6, 4),
            mode="lazy_full",
            landmarks=table,
            prefix_landmarks=3,
            full_landmarks=8,
        )
        staged = astar_search(
            grid,
            (0, 0),
            (6, 4),
            mode="staged",
            landmarks=table,
            prefix_landmarks=3,
            full_landmarks=8,
        )
        self.assertEqual(eager.stats.full_calls, eager.stats.manhattan_calls)
        self.assertEqual(eager.stats.pivot_evaluations, eager.stats.full_calls * 8)
        self.assertEqual(
            eager.stats.distance_table_reads, 8 + eager.stats.pivot_evaluations
        )
        self.assertEqual((eager.stats.prefix_calls, eager.stats.suffix_calls), (0, 0))
        self.assertEqual(lazy.stats.requeues, lazy.stats.full_calls - 1)
        self.assertEqual(lazy.stats.pivot_evaluations, lazy.stats.full_calls * 8)
        self.assertEqual(
            lazy.stats.distance_table_reads, 8 + lazy.stats.pivot_evaluations
        )
        self.assertEqual((lazy.stats.prefix_calls, lazy.stats.suffix_calls), (0, 0))
        self.assertEqual(
            staged.stats.requeues,
            staged.stats.prefix_calls + staged.stats.suffix_calls - 2,
        )
        self.assertEqual(
            staged.stats.pivot_evaluations,
            staged.stats.prefix_calls * 3 + staged.stats.suffix_calls * 5,
        )
        self.assertEqual(
            staged.stats.distance_table_reads, 8 + staged.stats.pivot_evaluations
        )
        self.assertEqual(staged.stats.full_calls, 0)
        for result in (eager, lazy, staged):
            self.assertGreaterEqual(result.stats.max_open_entries, 1)
            self.assertGreaterEqual(result.stats.max_live_states, 1)
            self.assertGreaterEqual(result.stats.search_ns, result.stats.stage_ns)
            self.assertEqual(result.stats.stage_ns, 0)

    def test_published_lazy_start_and_goal_contract_on_three_cell_path(self) -> None:
        grid = _grid("...")
        table = build_landmark_table(grid, 3)
        eager = astar_search(
            grid,
            (0, 0),
            (2, 0),
            mode="eager_full",
            landmarks=table,
            prefix_landmarks=1,
            full_landmarks=3,
        )
        lazy = astar_search(
            grid,
            (0, 0),
            (2, 0),
            mode="lazy_full",
            landmarks=table,
            prefix_landmarks=1,
            full_landmarks=3,
        )
        staged = astar_search(
            grid,
            (0, 0),
            (2, 0),
            mode="staged",
            landmarks=table,
            prefix_landmarks=1,
            full_landmarks=3,
        )
        self.assertEqual((eager.cost, lazy.cost, staged.cost), (2, 2, 2))
        self.assertEqual(
            (eager.stats.expanded, lazy.stats.expanded, staged.stats.expanded),
            (2, 2, 2),
        )
        self.assertEqual((eager.stats.full_calls, lazy.stats.full_calls), (3, 2))
        self.assertEqual((lazy.stats.pops, lazy.stats.requeues), (4, 1))
        self.assertEqual((staged.stats.prefix_calls, staged.stats.suffix_calls), (2, 2))
        self.assertEqual((staged.stats.pops, staged.stats.requeues), (5, 2))
        self.assertEqual(
            len(
                {eager.expansion_digest, lazy.expansion_digest, staged.expansion_digest}
            ),
            1,
        )

    def test_search_hot_path_uses_validated_raw_packed_access(self) -> None:
        grid = _grid(".......", ".@@.@..", "...@...", "..@....", ".......")
        table = build_landmark_table(grid, 8)
        with patch.object(
            LandmarkTable,
            "distance",
            side_effect=AssertionError("public checked accessor entered hot path"),
        ):
            result = astar_search(
                grid,
                (0, 0),
                (6, 4),
                mode="staged",
                landmarks=table,
                prefix_landmarks=3,
                full_landmarks=8,
            )
        self.assertTrue(result.found)

    def test_stage_timing_is_opt_in(self) -> None:
        grid = _grid(".......", ".@@.@..", "...@...", "..@....", ".......")
        table = build_landmark_table(grid, 8)
        default = astar_search(
            grid,
            (0, 0),
            (6, 4),
            mode="staged",
            landmarks=table,
            prefix_landmarks=3,
            full_landmarks=8,
        )
        measured = astar_search(
            grid,
            (0, 0),
            (6, 4),
            mode="staged",
            landmarks=table,
            prefix_landmarks=3,
            full_landmarks=8,
            measure_stage_time=True,
        )
        self.assertEqual(default.stats.stage_ns, 0)
        self.assertGreaterEqual(measured.stats.stage_ns, 0)
        self.assertGreaterEqual(measured.stats.search_ns, measured.stats.stage_ns)

    def test_better_g_reuses_query_independent_heuristic_cache(self) -> None:
        grid = _grid(
            ".@.......@",
            ".@.....@@@",
            "...@....@.",
            "@@...@....",
            "..@.......",
            "..........",
            "........@.",
            ".@..@@.@..",
            "....@@@..@",
            "..........",
        )
        table = build_landmark_table(grid, 16)
        eager = astar_search(
            grid,
            (0, 0),
            (9, 9),
            mode="eager_full",
            landmarks=table,
            prefix_landmarks=4,
            full_landmarks=16,
        )
        self.assertEqual(eager.cost, 18)
        self.assertGreater(eager.stats.heuristic_cache_hits, 0)
        self.assertGreater(eager.stats.relaxations, eager.stats.unique_discovered - 1)
        self.assertEqual(eager.stats.manhattan_calls, eager.stats.unique_discovered)
        self.assertEqual(eager.stats.full_calls, eager.stats.unique_discovered)

    def test_unreachable_queries_terminate_without_a_path(self) -> None:
        grid = _grid("..@..")
        table = build_landmark_table(grid, 4)
        for mode in ("manhattan", "eager_full", "lazy_full", "staged"):
            result = astar_search(
                grid,
                (0, 0),
                (4, 0),
                mode=mode,
                landmarks=None if mode == "manhattan" else table,
                prefix_landmarks=2,
                full_landmarks=4,
            )
            self.assertFalse(result.found)
            self.assertIsNone(result.cost)
            self.assertEqual(result.path, ())

    def test_search_and_path_validation_fail_closed(self) -> None:
        grid = _grid("...", ".@.")
        other_grid = _grid("...", "...")
        table = build_landmark_table(grid, 3)
        shallow_table = build_landmark_table(grid, 1)
        cases = (
            {"mode": "unknown", "landmarks": table},
            {"mode": "eager_full", "landmarks": None},
            {"mode": "eager_full", "landmarks": build_landmark_table(other_grid, 3)},
            {"mode": "eager_full", "landmarks": shallow_table},
            {"mode": "staged", "landmarks": table, "prefix_landmarks": -1},
            {
                "mode": "staged",
                "landmarks": table,
                "prefix_landmarks": 3,
                "full_landmarks": 2,
            },
            {
                "mode": "staged",
                "landmarks": table,
                "measure_stage_time": 1,
            },
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(SearchInputError):
                    arguments = {"full_landmarks": 3, **kwargs}
                    astar_search(
                        grid,
                        (0, 0),
                        (2, 0),
                        **arguments,
                    )
        with self.assertRaises(SearchInputError):
            astar_search(grid, (0, 0), (1, 1), mode="manhattan")
        with self.assertRaises(PathValidationError):
            validate_path(grid, ((0, 0), (2, 0)), (0, 0), (2, 0), 1)
        with self.assertRaises(PathValidationError):
            validate_path(grid, [(0, 0), (1, 0)], (0, 0), (1, 0), 1)  # type: ignore[arg-type]

    def test_landmark_table_constructor_rejects_mutable_or_misaligned_rows(
        self,
    ) -> None:
        grid = _grid("..")
        with self.assertRaises(ValueError):
            LandmarkTable(grid, ((0, 0),), (bytearray(8),))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            LandmarkTable(grid, ((0, 0),), (b"\x00" * 4,))


if __name__ == "__main__":
    unittest.main()
