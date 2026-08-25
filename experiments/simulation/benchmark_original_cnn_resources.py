#!/usr/bin/env python
"""CPU/GPU resource benchmark for the original WPDC StarBogusNet.

This benchmark imports the original CNN architecture from the CSST working
directory without modifying that pipeline.  It uses the architecture's exact
25x25 cutout and 17-feature inputs, default 30 epochs, training batch size 64,
and inference batch size 256.  Input values are deterministic normalized
representative tensors: resource use depends on tensor geometry, batch size and
network implementation, not source labels.  It must not be interpreted as a
new accuracy experiment.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psutil
import torch
from torch import nn, optim
from torch.utils.data import DataLoader, TensorDataset


def load_original(path: Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("wpdc_original_cnn", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_inputs(samples: int, seed: int):
    rng = np.random.default_rng(seed)
    # The source code applies robust per-cutout normalization before CNN input.
    cutouts = rng.normal(0.0, 1.0, size=(samples, 1, 25, 25)).astype(np.float32)
    features = rng.normal(0.0, 1.0, size=(samples, 17)).astype(np.float32)
    labels = rng.integers(0, 2, size=samples, dtype=np.int64).astype(np.float32)
    split = int(samples * 0.75)
    return cutouts[:split], features[:split], labels[:split], cutouts[split:], features[split:], labels[split:]


def sync(device):
    if device.type == "cuda": torch.cuda.synchronize(device)


def run_one(module, device, arrays, epochs, train_batch, infer_batch, seed):
    trn_cut, trn_feat, trn_y, val_cut, val_feat, val_y = arrays
    torch.manual_seed(seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(seed)
        torch.cuda.empty_cache()
        # This installed PyTorch build accepts the current device only (and
        # rejects both torch.device and integer arguments).
        torch.cuda.reset_peak_memory_stats()
    process = psutil.Process(os.getpid())
    rss_before = process.memory_info().rss
    model = module._StarBogusNet(cutout_size=25, n_feat=17).to(device)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    train_loader = DataLoader(TensorDataset(torch.from_numpy(trn_cut), torch.from_numpy(trn_feat), torch.from_numpy(trn_y)), batch_size=train_batch, shuffle=True, num_workers=0, generator=torch.Generator().manual_seed(seed))
    val_loader = DataLoader(TensorDataset(torch.from_numpy(val_cut), torch.from_numpy(val_feat), torch.from_numpy(val_y)), batch_size=infer_batch, shuffle=False, num_workers=0)
    sync(device); t0 = time.perf_counter()
    for _ in range(epochs):
        model.train()
        for images, features, labels in train_loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(images.to(device), features.to(device))
            loss = criterion(logits, labels.to(device)); loss.backward(); optimizer.step()
        model.eval()
        with torch.no_grad():
            for images, features, _ in val_loader:
                model(images.to(device), features.to(device))
    sync(device); train_s = time.perf_counter() - t0
    model.eval()
    # Three timed inference repeats after a warm-up transfer/inference.
    with torch.no_grad():
        for images, features, _ in val_loader:
            model(images.to(device), features.to(device))
    sync(device)
    infer_times = []
    for _ in range(3):
        sync(device); start = time.perf_counter()
        with torch.no_grad():
            for images, features, _ in val_loader:
                model(images.to(device), features.to(device))
        sync(device); infer_times.append(time.perf_counter() - start)
    rss_after = process.memory_info().rss
    result = {
        "device": str(device), "samples_total": int(len(trn_cut) + len(val_cut)), "samples_train": int(len(trn_cut)), "samples_inference": int(len(val_cut)),
        "epochs": int(epochs), "train_batch": int(train_batch), "inference_batch": int(infer_batch),
        "training_s": float(train_s), "training_s_per_epoch": float(train_s / epochs),
        "inference_s_median": float(np.median(infer_times)), "inference_s_per_1k_candidates": float(np.median(infer_times) / len(val_cut) * 1000),
        "inference_repeats_s": [float(x) for x in infer_times], "process_rss_delta_mb": float((rss_after-rss_before)/1024**2),
        "parameter_count": int(sum(p.numel() for p in model.parameters())),
    }
    if device.type == "cuda":
        result.update({"gpu_peak_allocated_mb": float(torch.cuda.max_memory_allocated()/1024**2), "gpu_peak_reserved_mb": float(torch.cuda.max_memory_reserved()/1024**2), "gpu_name": torch.cuda.get_device_name(0)})
    else:
        result.update({"gpu_peak_allocated_mb": None, "gpu_peak_reserved_mb": None, "gpu_name": "N/A (CPU run)"})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Original WPDC source file containing _StarBogusNet")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=2048)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(); args.output_dir.mkdir(parents=True, exist_ok=True)
    if not args.source.exists(): raise FileNotFoundError(args.source)
    module = load_original(args.source)
    arrays = make_inputs(args.samples, args.seed)
    devices = [torch.device("cpu")]
    if torch.cuda.is_available(): devices.append(torch.device("cuda:0"))
    results = [run_one(module, device, arrays, args.epochs, 64, 256, args.seed) for device in devices]
    payload = {"protocol": {"source": str(args.source), "architecture": "original _StarBogusNet imported unchanged", "input": "deterministic normalized representative tensors; 25x25x1 cutout + 17 features", "scope": "resource-only; no accuracy claim", "seed": args.seed}, "environment": {"torch": torch.__version__, "cuda_available": bool(torch.cuda.is_available()), "cuda_version": torch.version.cuda}, "results": results}
    (args.output_dir / "cnn_cpu_gpu_resource_benchmark.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = ["# Original WPDC CNN CPU/GPU resource benchmark", "", "The original `_StarBogusNet` is imported unchanged. Inputs retain the original tensor geometry and defaults, but are deterministic normalized representative tensors; this is a resource benchmark, not an accuracy experiment.", "", "| Device | Training / s | Training / epoch s | Inference / ms per 1k candidates | Process RSS delta / MB | GPU peak allocated / MB | GPU peak reserved / MB |", "|---|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        gpu_a = "N/A" if r["gpu_peak_allocated_mb"] is None else f"{r['gpu_peak_allocated_mb']:.1f}"
        gpu_r = "N/A" if r["gpu_peak_reserved_mb"] is None else f"{r['gpu_peak_reserved_mb']:.1f}"
        lines.append(f"| {r['device']} | {r['training_s']:.3f} | {r['training_s_per_epoch']:.3f} | {r['inference_s_per_1k_candidates']*1000:.3f} | {r['process_rss_delta_mb']:.1f} | {gpu_a} | {gpu_r} |")
    (args.output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__": main()
