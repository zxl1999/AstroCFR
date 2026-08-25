#!/usr/bin/env python
"""Verified concurrent HTTP-Range downloader for STScI public FLC files."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import subprocess

import requests

S3_PUBLIC = "https://stpubdata.s3.amazonaws.com/hst/public"


def fetch(url: str, start: int, end: int, output: Path) -> int:
    expected = end - start + 1
    for attempt in range(1, 9):
        existing = output.stat().st_size if output.exists() else 0
        if existing == expected:
            return expected
        if existing > expected:
            output.unlink()
            existing = 0
        try:
            # Keep archive attempts short: on this network a stalled TLS
            # stream otherwise prevents the resumable curl fallback from
            # preserving any partial range for several minutes.
            response = requests.get(url, headers={"Range": f"bytes={start + existing}-{end}"},
                                    stream=True, timeout=(15, 35))
            response.raise_for_status()
            if response.status_code != 206:
                raise RuntimeError(f"Range request not honoured: {response.status_code}")
            with output.open("ab" if existing else "wb") as stream:
                for block in response.iter_content(1024 * 1024):
                    if block:
                        stream.write(block)
        except requests.RequestException:
            # The STScI archive sometimes terminates Python TLS streams after
            # a partial response.  Curl's HTTP/1.1 path is more resilient;
            # append only the bytes returned for the remaining sub-range.
            temporary = output.with_suffix(output.suffix + ".curltmp")
            temporary.unlink(missing_ok=True)
            try:
                subprocess.run(["curl.exe", "--http1.1", "-k", "-L",
                                "--connect-timeout", "15", "--max-time", "75",
                                "-sS", "-r", f"{start + existing}-{end}", url,
                                "-o", str(temporary)],
                               timeout=90, check=False)
                if temporary.exists() and temporary.stat().st_size:
                    with temporary.open("rb") as source, output.open("ab" if existing else "wb") as target:
                        while block := source.read(1024 * 1024):
                            target.write(block)
            finally:
                temporary.unlink(missing_ok=True)
    actual = output.stat().st_size if output.exists() else 0
    raise RuntimeError(f"short range {start}-{end}: {actual} != {expected} after retries")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("filename")
    parser.add_argument("size", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--chunk-mb", type=float, default=8,
                        help="range size in MiB; fractions permit resilient slow-link retries")
    parser.add_argument("--s3-size", type=int,
                        help="authoritative Content-Length from the public S3 object; overrides stale MAST size")
    parser.add_argument("--url",
                        help="explicit HTTP Range source; useful for public HLSP catalogues outside S3")
    args = parser.parse_args()
    if args.s3_size is not None:
        args.size = args.s3_size
    url = args.url or f"{S3_PUBLIC}/{args.filename[:4]}/{args.filename[:9]}/{args.filename}"
    chunk = max(64 * 1024, int(round(args.chunk_mb * 1024 * 1024)))
    ranges = [(offset, min(args.size - 1, offset + chunk - 1)) for offset in range(0, args.size, chunk)]
    pieces = args.output.with_suffix(args.output.suffix + ".ranges")
    pieces.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(fetch, url, start, end, pieces / f"{start:012d}_{end:012d}.part"): (start, end)
                   for start, end in ranges}
        for future in as_completed(pending):
            start, end = pending[future]
            print(f"range {start}-{end}: {future.result()} bytes", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    assembling = args.output.with_suffix(args.output.suffix + ".assembling")
    with assembling.open("wb") as merged:
        for start, end in ranges:
            piece = pieces / f"{start:012d}_{end:012d}.part"
            if piece.stat().st_size != end - start + 1:
                raise RuntimeError(f"bad piece: {piece}")
            with piece.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    merged.write(block)
    if assembling.stat().st_size != args.size:
        raise RuntimeError("assembled file size mismatch")
    assembling.replace(args.output)
    print(f"complete {args.output} {args.output.stat().st_size}", flush=True)


if __name__ == "__main__":
    main()
