#!/usr/bin/env python
"""Measure AstroCFR tile-level CPU scaling on a public HST/ACS crop.

The expensive empirical-PSF/residual-deblend branch is applied independently
to equal-sized image tiles.  Runs with 1, 2, and 4 worker processes receive the
same pixels and parameters.  The benchmark includes worker start-up and tile
processing, while FITS download and catalogue evaluation are excluded.

This is a workstation weak-scaling diagnostic, not evidence for TB/PB survey
throughput.  Supply the original experiment/data directory with --upstream or
ASTROCFR_UPSTREAM; large public HST files are not stored in Git.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import psutil


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results" / "hst_tile_parallel_scaling"
_IMAGE: np.ndarray | None = None
_RMS: float | None = None
_FWHM: float | None = None
_BENCH = None


def resolve_upstream(value: str | None) -> Path:
    candidate = value or os.environ.get("ASTROCFR_UPSTREAM")
    if not candidate:
        raise SystemExit("Provide --upstream PATH or set ASTROCFR_UPSTREAM.")
    path = Path(candidate).expanduser().resolve()
    if not (path / "hst_unified_baseline_benchmark.py").exists():
        raise SystemExit(f"No upstream HST benchmark found in {path}")
    return path


def initialise_worker(upstream: str, cluster: str) -> None:
    global _IMAGE, _RMS, _FWHM, _BENCH
    sys.path.insert(0, upstream)
    import hst_acsggct_benchmark as hst
    import hst_unified_baseline_benchmark as bench
    import real_data_domain_adaptation as adapt
    import real_data_zero_shot_generalization as base

    image, _ = hst.read_cluster(cluster)
    _IMAGE, _RMS = base.estimate_background(image)
    preliminary = base.detect_sources(_IMAGE, _RMS, fwhm=2.0, threshold_sigma=10.0)
    module = adapt.load_pipeline()
    _FWHM = float(np.clip(module.estimate_psf_fwhm(_IMAGE, preliminary, _RMS,
                                                   min_snr=20, max_sources=40), 1.5, 4.0))
    _BENCH = bench


def run_tile(bounds: tuple[int, int, int, int]) -> dict:
    if _IMAGE is None or _BENCH is None or _RMS is None or _FWHM is None:
        raise RuntimeError("worker was not initialized")
    x0, y0, x1, y1 = bounds
    tile = np.ascontiguousarray(_IMAGE[y0:y1, x0:x1])
    start = time.perf_counter()
    xy, _ = _BENCH.wpdc_deblend(tile, _RMS, _FWHM)
    return {"bounds": bounds, "candidates": int(len(xy)),
            "runtime_s_worker": float(time.perf_counter() - start)}


def process_memory_monitor(stop: threading.Event, peak: list[float]) -> None:
    parent = psutil.Process()
    while not stop.wait(.02):
        processes = [parent] + parent.children(recursive=True)
        total = 0
        for process in processes:
            try:
                total += process.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        peak[0] = max(peak[0], total / 1024 ** 2)


def one_run(upstream: Path, cluster: str, tiles: list[tuple[int, int, int, int]], workers: int) -> dict:
    stop = threading.Event(); peak = [0.0]
    monitor = threading.Thread(target=process_memory_monitor, args=(stop, peak), daemon=True)
    monitor.start(); start = time.perf_counter()
    try:
        with ProcessPoolExecutor(max_workers=workers, initializer=initialise_worker,
                                 initargs=(str(upstream), cluster)) as pool:
            rows = list(pool.map(run_tile, tiles))
    finally:
        elapsed = time.perf_counter() - start
        stop.set(); monitor.join()
    pixels = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in tiles)
    return {"workers": workers, "wall_s": float(elapsed),
            "throughput_mpix_s": float(pixels / 1e6 / elapsed),
            "aggregate_peak_rss_mb": float(peak[0]),
            "candidate_total": int(sum(row["candidates"] for row in rows)),
            "sum_worker_compute_s": float(sum(row["runtime_s_worker"] for row in rows))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream")
    parser.add_argument("--cluster", default="ngc6752")
    parser.add_argument("--tile-size", type=int, default=300)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", default="1,2,4")
    parser.add_argument("--existing-results", help="merge prior runs from a compatible JSON file")
    args = parser.parse_args()
    upstream = resolve_upstream(args.upstream)
    workers = [int(value) for value in args.workers.split(",")]
    image_size = 1200
    if image_size % args.tile_size:
        raise SystemExit("tile size must divide the registered 1200-pixel crop")
    tiles = [(x, y, x + args.tile_size, y + args.tile_size)
             for y in range(0, image_size, args.tile_size)
             for x in range(0, image_size, args.tile_size)]

    runs: list[dict] = []
    if args.existing_results:
        existing = json.loads(Path(args.existing_results).read_text(encoding="utf-8"))
        previous_protocol = existing.get("protocol", {})
        if previous_protocol.get("cluster") != args.cluster or previous_protocol.get("tile_size_px") != args.tile_size:
            raise SystemExit("existing results use a different cluster or tile size")
        runs.extend(existing.get("runs", []))
    # Interleave worker counts by repeat so thermal drift does not consistently
    # favour one configuration.
    for repeat in range(args.repeats):
        order = workers if repeat % 2 == 0 else list(reversed(workers))
        for count in order:
            result = one_run(upstream, args.cluster, tiles, count)
            result["repeat"] = repeat
            runs.append(result)
            print(json.dumps(result))

    if not any(row["workers"] == 1 for row in runs):
        raise SystemExit("A 1-worker run is required to calculate speedup.")
    summary = []
    all_workers = sorted({row["workers"] for row in runs})
    serial_median = statistics.median(row["wall_s"] for row in runs if row["workers"] == 1)
    for count in all_workers:
        subset = [row for row in runs if row["workers"] == count]
        wall = statistics.median(row["wall_s"] for row in subset)
        throughput = statistics.median(row["throughput_mpix_s"] for row in subset)
        memory = statistics.median(row["aggregate_peak_rss_mb"] for row in subset)
        candidates = sorted({row["candidate_total"] for row in subset})
        summary.append({"workers": count, "wall_s_median": float(wall),
                        "wall_s_range": [float(min(row["wall_s"] for row in subset)),
                                         float(max(row["wall_s"] for row in subset))],
                        "speedup_vs_1": float(serial_median / wall),
                        "parallel_efficiency": float(serial_median / wall / count),
                        "throughput_mpix_s_median": float(throughput),
                        "aggregate_peak_rss_mb_median": float(memory),
                        "candidate_totals": candidates})

    payload = {
        "protocol": {"cluster": args.cluster, "crop_px": [1200, 1200],
                     "tile_size_px": args.tile_size, "tile_count": len(tiles),
                     "workload": "AstroCFR empirical-ePSF plus residual-deblend branch",
                     "new_repeats": args.repeats, "worker_counts": all_workers,
                     "timing_scope": "process startup, per-worker image/background/PSF initialization, and all tile processing",
                     "memory_scope": "aggregate RSS of parent plus descendant worker processes",
                     "excluded": "data download and catalogue evaluation",
                     "claim_boundary": "single-workstation tile-level scaling only; no TB/PB extrapolation"},
        "runs": runs, "summary": summary,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tile_parallel_scaling.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))


if __name__ == "__main__":
    main()
