#!/usr/bin/env python
"""Download the registered ten real non-globular ACS/WFC fields from MAST.

The downloader is resumable and writes outside the Git package under
``external/non_globular_fields``.  It downloads only CTE-corrected FLC science
files; catalogues and PSF libraries remain separate admission-gate inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

API = "https://mast.stsci.edu/api/v0/invoke"
DOWNLOAD = "https://mast.stsci.edu/api/v0.1/Download/file"
S3_PUBLIC = "https://stpubdata.s3.amazonaws.com/hst/public"
CACHE_DIR = Path("external/non_globular_fields/.mast_product_cache")
PROGRAM_KEY = {"12055": "phat_m31", "14610": "phatter_m33", "12939": "http_tarantula", "10915": "angst"}


def product_list(obsid: int) -> list[dict[str, Any]]:
    cache = CACHE_DIR / f"{obsid}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    request = {"service": "Mast.Caom.Products", "format": "json", "params": {"obsid": str(obsid)}, "pagesize": 1000, "page": 1}
    last_error: Exception | None = None
    for attempt in range(1, 11):
        try:
            response = requests.post(API, data={"request": json.dumps(request)}, timeout=120)
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "COMPLETE":
                raise RuntimeError(f"MAST product query failed for {obsid}: {payload}")
            data = payload.get("data", [])
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
            return data
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            # Some Windows TLS paths intermittently terminate MAST API POSTs.
            # curl's HTTP/1.1 implementation is materially more reliable on
            # those paths, so cache its normalized product list as a fallback.
            try:
                encoded = requests.utils.quote(json.dumps(request), safe="")
                result = subprocess.run(
                    ["curl.exe", "--http1.1", "-k", "-L", "--connect-timeout", "15",
                     "--max-time", "75", "-sS", "-X", "POST",
                     "-H", "Content-Type: application/x-www-form-urlencoded",
                     "--data", f"request={encoded}", API],
                    capture_output=True, text=True, timeout=90, check=True,
                )
                payload = json.loads(result.stdout)
                data = payload.get("data", [])
                if payload.get("status") == "COMPLETE" and data:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(data), encoding="utf-8")
                    return data
            except (OSError, subprocess.SubprocessError, ValueError) as curl_exc:
                last_error = curl_exc
            if attempt == 10: break
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"MAST product query exhausted retries for {obsid}") from last_error


def product_lists(obsids: list[int], workers: int = 3) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(product_list, obsid): obsid for obsid in obsids}
        for future in as_completed(futures):
            obsid = futures[future]
            out[obsid] = future.result()
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download(uri: str, path: Path, expected_size: int | None, session: requests.Session) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    for attempt in range(1, 31):
        existing = part.stat().st_size if part.exists() else 0
        if expected_size is not None and existing == expected_size:
            break
        if expected_size is not None and existing > expected_size:
            part.unlink(); existing = 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        try:
            with session.get(DOWNLOAD, params={"uri": uri}, headers=headers, stream=True, timeout=(30, 300)) as response:
                if existing and response.status_code != 206:
                    part.unlink(missing_ok=True); existing = 0
                    raise requests.RequestException("server ignored Range; restarting partial file")
                response.raise_for_status()
                mode = "ab" if existing else "wb"
                with part.open(mode) as out:
                    for block in response.iter_content(1024 * 1024):
                        if block: out.write(block)
        except requests.RequestException as exc:
            # Preserve the .part file and ask curl to resume it.  It is not
            # renamed until its advertised MAST size has been verified.
            try:
                url = f"{DOWNLOAD}?uri={requests.utils.quote(uri, safe=':')}"
                subprocess.run(["curl.exe", "--http1.1", "-k", "-L", "-C", "-",
                                "--connect-timeout", "15", "--max-time", "300",
                                "--retry", "3", "--retry-all-errors", "-sS", url,
                                "-o", str(part)], check=True, timeout=330)
                if expected_size is None or part.stat().st_size == expected_size:
                    break
            except (OSError, subprocess.SubprocessError):
                pass
            if attempt == 30: raise
            current = part.stat().st_size if part.exists() else 0
            print(f"  retry {attempt}/30 after {current} bytes: {type(exc).__name__}", flush=True)
            time.sleep(min(2 * attempt, 30))
            continue
        if expected_size is None or part.stat().st_size == expected_size:
            break
    if expected_size is not None and part.stat().st_size != expected_size:
        raise RuntimeError(f"size mismatch for {path.name}: {part.stat().st_size} != {expected_size}")
    for rename_attempt in range(20):
        try:
            part.replace(path)
            break
        except PermissionError:
            if rename_attempt == 19:
                # Windows antivirus/indexing can hold a completed .part file
                # open while still allowing a normal copy.  Copy, verify, and
                # remove the temporary file rather than marking it complete by
                # filename alone.
                shutil.copyfile(part, path)
                if expected_size is not None and path.stat().st_size != expected_size:
                    raise RuntimeError(f"copy size mismatch for {path.name}")
                part.unlink(missing_ok=True)
                break
            time.sleep(1.0)


def download_s3_public(filename: str, path: Path, expected_size: int | None) -> bool:
    """Resume from STScI's public S3 mirror (often faster than MAST API)."""
    root = filename[:4]
    url = f"{S3_PUBLIC}/{root}/{filename[:9]}/{filename}"
    part = path.with_suffix(path.suffix + ".part")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["curl.exe", "--http1.1", "-k", "-L", "-C", "-",
                        "--connect-timeout", "15", "--max-time", "1200",
                        "--retry", "5", "--retry-all-errors", "-sS", url,
                        "-o", str(part)], check=True, timeout=1230)
        if expected_size is not None and part.stat().st_size != expected_size:
            return False
        part.replace(path)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fields", nargs="+", default=None,
                        help="field ids; default is all ten fields in candidate-config order")
    parser.add_argument("--inventory", type=Path, default=Path("results/non_globular_field_inventory.json"))
    parser.add_argument("--candidate-config", type=Path, default=Path("configs/non_globular_field_candidates.json"))
    parser.add_argument("--output-root", type=Path, default=Path("external/non_globular_fields"))
    parser.add_argument("--max-files-per-field", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    candidate_rows = json.loads(args.candidate_config.read_text(encoding="utf-8"))["candidates"]
    candidates = {x["field_id"]: x for x in candidate_rows}
    selected_fields = args.fields or [x["field_id"] for x in candidate_rows]
    session = requests.Session()
    manifest: list[dict[str, Any]] = []
    pending: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for field_id in selected_fields:
        if field_id not in candidates: raise KeyError(f"unknown field: {field_id}")
        c = candidates[field_id]
        key = PROGRAM_KEY[c["proposal_id"]]
        rows = [r for r in inventory[key]["rows"] if r.get("obs_collection") == "HST" and r.get("target_name") == c["target_name"] and str(r.get("filters", "")).upper() == c["preferred_filter"]]
        for row in rows:
            pending.append((field_id, c, row))
    product_map = product_lists(sorted({int(row["obsid"]) for _, _, row in pending}))
    grouped: dict[str, list[tuple[dict[str, Any], list[dict[str, Any]]]]] = {}
    for field_id, c, row in pending:
        products = []
        seen_group: set[str] = set()
        for prod in product_map[int(row["obsid"])] :
            filename = str(prod.get("productFilename", ""))
            uri = str(prod.get("dataURI", ""))
            # HAP reprocessed products beginning hst_ duplicate the native
            # j-root FLC exposure and must not be downloaded a second time.
            if not (filename.lower().endswith("_flc.fits") and not filename.lower().startswith("hst_") and prod.get("productType") == "SCIENCE" and uri and uri not in seen_group): continue
            seen_group.add(uri); products.append(prod)
        grouped.setdefault(field_id, []).append((row, products))
    for field_id in selected_fields:
        c = candidates[field_id]
        groups = grouped[field_id]
        coherent = sorted(groups, key=lambda item: (-len(item[1]), int(item[0]["obsid"])))
        if coherent and len(coherent[0][1]) >= 4:
            selected_pairs = [(coherent[0][0], p) for p in coherent[0][1][:args.max_files_per_field]]
        else:
            selected_pairs = []
            seen: set[str] = set()
            for row, products in sorted(groups, key=lambda item: int(item[0]["obsid"])):
                for prod in products:
                    uri = str(prod["dataURI"])
                    if uri in seen: continue
                    seen.add(uri); selected_pairs.append((row, prod))
                    if len(selected_pairs) >= args.max_files_per_field: break
                if len(selected_pairs) >= args.max_files_per_field: break
        for row, prod in selected_pairs:
            manifest.append({"field_id": field_id, "target_name": c["target_name"], "proposal_id": c["proposal_id"], "filter": c["preferred_filter"], "source_obsid": row["obsid"], "product_filename": prod["productFilename"], "data_uri": prod["dataURI"], "size": prod.get("size"), "status": "planned"})
    total = sum(int(x["size"] or 0) for x in manifest)
    print(f"Selected {len(manifest)} unique FLC files, {total / 1024**3:.2f} GiB")
    by_field: dict[str, list[dict[str, Any]]] = {}
    for item in manifest: by_field.setdefault(item["field_id"], []).append(item)
    for field_id, items in by_field.items():
        print(f"  {field_id}: {len(items)} files, {sum(int(x['size'] or 0) for x in items) / 1024**3:.2f} GiB")
    if args.dry_run:
        return
    args.output_root.mkdir(parents=True, exist_ok=True)
    for i, item in enumerate(manifest, 1):
        target = args.output_root / item["field_id"] / "flc" / item["product_filename"]
        if target.exists() and item["size"] and target.stat().st_size == int(item["size"]):
            try:
                target.with_suffix(target.suffix + ".part").unlink(missing_ok=True)
            except PermissionError:
                pass
            item["status"] = "verified_existing"; item["sha256"] = sha256(target)
        else:
            print(f"[{i}/{len(manifest)}] {item['field_id']} {item['product_filename']}", flush=True)
            expected = int(item["size"]) if item["size"] else None
            # The native public S3 archive exposes HTTP byte ranges and is
            # substantially more reliable for these large archival FLCs.
            # Preserve the MAST API implementation as a verified fallback.
            if not download_s3_public(item["product_filename"], target, expected):
                download(item["data_uri"], target, expected, session)
            item["status"] = "downloaded"; item["sha256"] = sha256(target)
        item["local_path"] = str(target).replace("\\", "/")
    (args.output_root / "download_manifest.json").write_text(json.dumps({"metadata_only": False, "files": manifest}, indent=2), encoding="utf-8")
    print(args.output_root / "download_manifest.json")


if __name__ == "__main__": main()
