#!/usr/bin/env python
"""Fair simulation-domain candidate-classifier ablation for WPDC.

This experiment deliberately leaves candidate generation, source association,
and the WPDC ePSF/deblending stages unchanged.  It compares three classifiers
on exactly the same detector-stage candidates from the four CSST-like chips:

* the 17-feature RandomForest used by the lightweight WPDC branch;
* the original WPDC ``_StarBogusNet`` (25 x 25 cutout + 17 features);
* a small patch Transformer with the same two input streams.

Candidates within 2 pixels of a simulated reference are positive, candidates
farther than 8 pixels are negative, and the ambiguous annulus is discarded.
The 60/20/20 stratified split keeps a validation set for threshold selection
and an untouched test set for the reported metrics.  This is an architecture
ablation, not a comparison to a different astronomical task or a SOTA claim.
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
from astropy.table import Table
from scipy.spatial import cKDTree
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (average_precision_score, precision_recall_fscore_support,
                             precision_score, recall_score, roc_auc_score)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from torch import nn, optim
from torch.utils.data import DataLoader, TensorDataset


CHIPS = (12, 13, 17, 18)
SIM_PREFIX = "CSST_MSC_MS_WIDE_20280101000000_20280101000230_10100300001"
SEED = 20260806


def load_module(path: Path, name: str):
    # The archived WPDC source imports sibling helper modules.
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PatchTransformer(nn.Module):
    """A compact ViT-style image branch fused with WPDC's 17 features."""
    def __init__(self, n_feat: int = 17, embed_dim: int = 64, depth: int = 2,
                 heads: int = 4, patch: int = 5):
        super().__init__()
        self.patch_embed = nn.Conv2d(1, embed_dim, kernel_size=patch, stride=patch)
        self.cls_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        self.position = nn.Parameter(torch.zeros(1, 26, embed_dim))  # 25 patches + CLS
        layer = nn.TransformerEncoderLayer(embed_dim, heads, dim_feedforward=128,
                                           dropout=0.10, activation="gelu",
                                           batch_first=True, norm_first=True)
        self.encoder = nn.TransformerEncoder(layer, num_layers=depth)
        self.norm = nn.LayerNorm(embed_dim)
        self.feat_mlp = nn.Sequential(nn.Linear(n_feat, 32), nn.GELU(),
                                      nn.Linear(32, 16), nn.GELU())
        self.head = nn.Sequential(nn.Linear(embed_dim + 16, 32), nn.GELU(),
                                  nn.Dropout(0.30), nn.Linear(32, 1))
        nn.init.trunc_normal_(self.cls_token, std=0.02)
        nn.init.trunc_normal_(self.position, std=0.02)

    def forward(self, images, features):
        tokens = self.patch_embed(images).flatten(2).transpose(1, 2)
        cls = self.cls_token.expand(images.shape[0], -1, -1)
        tokens = torch.cat([cls, tokens], dim=1) + self.position
        image_embedding = self.norm(self.encoder(tokens)[:, 0])
        return self.head(torch.cat([image_embedding, self.feat_mlp(features)], dim=1)).squeeze(1)


def read_reference(path: Path) -> Table:
    names = ['obj_ID','ID_chip','filter','xImage','yImage','ra','dec','ra_orig','dec_orig','z','mag','obj_type',
             'pm_ra','pm_dec','RV','parallax','av','stellarmass','dm','teff','logg','feh','bulgemass','diskmass',
             'detA','e1','e2','kappa','g1','g2','size','galType','veldisp']
    return Table.read(path, format="ascii", names=names)


def build_or_load_data(base, mod, simulation_dir: Path, cache: Path):
    """Regenerate cutouts using the same deterministic candidate protocol as RF."""
    if cache.exists():
        data = np.load(cache, allow_pickle=False)
        print(f"Loaded candidate cache: {cache}")
        return data["cutouts"], data["features"], data["labels"], data["groups"]
    rng = np.random.default_rng(SEED)
    cuts, feats_all, labels, groups = [], [], [], []
    for chip in CHIPS:
        t0 = time.perf_counter()
        image, _ = base.read_image(simulation_dir / f"{SIM_PREFIX}_{chip}_L0_V01.fits")
        reference = read_reference(simulation_dir / f"{SIM_PREFIX}_{chip}_L0_V01_top1000.cat")
        image_sub, rms = base.estimate_background(image)
        sources = base.detect_sources(image_sub, rms, fwhm=3.0, threshold_sigma=6.0)
        if sources is None or len(sources) == 0:
            raise RuntimeError(f"No detector candidates for chip {chip}")
        features, xy = base.source_features(mod, sources, image_sub, rms)
        dist, _ = cKDTree(np.column_stack([reference["xImage"], reference["yImage"]])).query(xy, k=1)
        pos = np.where(dist < 2.0)[0]
        neg = np.where(dist > 8.0)[0]
        nneg = min(len(neg), max(len(pos) * 5, 400), 2000)
        if len(neg) > nneg:
            neg = rng.choice(neg, nneg, replace=False)
        chosen = np.concatenate([pos, neg])
        cutouts = mod._extract_cutouts(sources, image_sub, 25)
        cuts.append(cutouts[chosen]); feats_all.append(features[chosen])
        labels.append(np.r_[np.ones(len(pos)), np.zeros(len(neg))])
        groups.append(np.full(len(chosen), chip, dtype=np.int16))
        print(f"Chip {chip}: candidates={len(sources)}, pos={len(pos)}, neg={len(neg)}, {time.perf_counter()-t0:.1f}s")
    cache.parent.mkdir(parents=True, exist_ok=True)
    out = (np.concatenate(cuts), np.concatenate(feats_all).astype(np.float32),
           np.concatenate(labels).astype(np.int64), np.concatenate(groups))
    np.savez_compressed(cache, cutouts=out[0], features=out[1], labels=out[2], groups=out[3])
    return out


def choose_threshold(y, probability, min_recall=0.90):
    """Highest validation threshold retaining the pre-registered recall floor."""
    choices = []
    for threshold in np.unique(probability):
        pred = probability >= threshold
        recall = recall_score(y, pred, zero_division=0)
        if recall >= min_recall:
            choices.append((precision_score(y, pred, zero_division=0), float(threshold)))
    return max(choices)[1] if choices else 0.5


def metrics(y, p, threshold):
    pred = p >= threshold
    precision, recall, f1, _ = precision_recall_fscore_support(y, pred, average="binary", zero_division=0)
    return {"threshold": float(threshold), "precision": float(precision), "recall": float(recall),
            "f1": float(f1), "auroc": float(roc_auc_score(y, p)),
            "auprc": float(average_precision_score(y, p)), "n_test": int(len(y)),
            "positive_test": int(np.sum(y))}


def probability(model, cutouts, features, device, batch_size=256):
    model.eval(); chunks = []
    with torch.no_grad():
        for start in range(0, len(cutouts), batch_size):
            sl = slice(start, min(start + batch_size, len(cutouts)))
            logits = model(torch.from_numpy(cutouts[sl]).to(device), torch.from_numpy(features[sl]).to(device))
            chunks.append(torch.sigmoid(logits).cpu().numpy())
    return np.concatenate(chunks)


def train_torch(model, arrays, device, seed, epochs=30, batch_size=64):
    cut_train, feat_train, y_train, cut_val, feat_val, y_val = arrays
    torch.manual_seed(seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(seed); torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats()
    generator = torch.Generator().manual_seed(seed)
    loader = DataLoader(TensorDataset(torch.from_numpy(cut_train), torch.from_numpy(feat_train), torch.from_numpy(y_train.astype(np.float32))),
                        batch_size=batch_size, shuffle=True, num_workers=0, generator=generator)
    val_loader = DataLoader(TensorDataset(torch.from_numpy(cut_val), torch.from_numpy(feat_val), torch.from_numpy(y_val.astype(np.float32))),
                            batch_size=256, shuffle=False, num_workers=0)
    pos_weight = torch.tensor([(len(y_train)-y_train.sum()) / max(y_train.sum(), 1) * 2.0], device=device, dtype=torch.float32)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    schedule = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    best_loss, best_state = float("inf"), None
    start = time.perf_counter()
    for epoch in range(epochs):
        model.train()
        for images, features, label in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(images.to(device), features.to(device)), label.to(device))
            loss.backward(); optimizer.step()
        schedule.step(); model.eval(); losses = []
        with torch.no_grad():
            for images, features, label in val_loader:
                losses.append(criterion(model(images.to(device), features.to(device)), label.to(device)).item())
        value = float(np.mean(losses))
        if value < best_loss:
            best_loss = value; best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        if (epoch + 1) % 10 == 0: print(f"  epoch {epoch+1:02d}/{epochs}: validation loss={value:.4f}")
    if best_state is not None: model.load_state_dict(best_state)
    if device.type == "cuda": torch.cuda.synchronize(device)
    elapsed = time.perf_counter() - start
    return elapsed, best_loss


def timed_inference(model, cutouts, features, device):
    _ = probability(model, cutouts[:min(512, len(cutouts))], features[:min(512, len(features))], device)
    if device.type == "cuda": torch.cuda.synchronize(device)
    timings = []
    for _ in range(3):
        if device.type == "cuda": torch.cuda.synchronize(device)
        start = time.perf_counter(); _ = probability(model, cutouts, features, device)
        if device.type == "cuda": torch.cuda.synchronize(device)
        timings.append(time.perf_counter()-start)
    return float(np.median(timings) / len(cutouts) * 1000)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Original WPDC source with StarBogusNet")
    parser.add_argument("--helper", type=Path, required=True, help="WPDC real_data_zero_shot_generalization.py")
    parser.add_argument("--simulation-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=30)
    args = parser.parse_args(); args.output_dir.mkdir(parents=True, exist_ok=True)
    torch.backends.cudnn.deterministic = True; torch.backends.cudnn.benchmark = False
    base = load_module(args.helper, "wpdc_sim_base")
    mod = load_module(args.source, "wpdc_original_for_transformer_ablation")
    cutouts, features, labels, groups = build_or_load_data(base, mod, args.simulation_dir, args.output_dir / "simulation_candidate_cutouts.npz")
    all_index = np.arange(len(labels))
    train_val, test = train_test_split(all_index, test_size=0.20, random_state=SEED, stratify=labels)
    train, validation = train_test_split(train_val, test_size=0.25, random_state=SEED, stratify=labels[train_val])
    scaler = StandardScaler().fit(features[train])
    scaled = scaler.transform(features).astype(np.float32)
    # RF receives precisely its established feature input, train split and threshold rule.
    start = time.perf_counter()
    rf = RandomForestClassifier(n_estimators=400, max_depth=15, min_samples_leaf=2, max_features="sqrt",
                                class_weight={0: 1, 1: 6}, random_state=SEED, n_jobs=-1)
    rf.fit(scaled[train], labels[train]); rf_train_s = time.perf_counter()-start
    rf_val = rf.predict_proba(scaled[validation])[:, 1]; rf_thr = choose_threshold(labels[validation], rf_val)
    rf_test_start = time.perf_counter(); rf_test = rf.predict_proba(scaled[test])[:, 1]
    rf_infer_ms = (time.perf_counter()-rf_test_start) / len(test) * 1000
    result = {"protocol": {"candidate_labels": "<2 px positive; >8 px negative; 2--8 px excluded", "chips": list(CHIPS),
               "split": "stratified 60/20/20 train/validation/test", "seed": SEED,
               "threshold": "highest validation threshold retaining >=90% recall", "input": "25x25 normalized cutout plus 17 handcrafted WPDC features"},
              "data": {"candidates": int(len(labels)), "positive": int(labels.sum()), "negative": int(len(labels)-labels.sum()),
                       "chips": {str(c): int(np.sum(groups == c)) for c in CHIPS}},
              "models": {"RandomForest": {"parameters": None, "training_s": float(rf_train_s), "inference_ms_per_candidate": float(rf_infer_ms),
                                              "test": metrics(labels[test], rf_test, rf_thr)}}}
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    process = psutil.Process(os.getpid())
    for label, constructor in (("Original_CNN", lambda: mod._StarBogusNet(cutout_size=25, n_feat=17)),
                               ("Patch_Transformer", lambda: PatchTransformer())):
        model = constructor().to(device); rss_before = process.memory_info().rss
        train_s, best_loss = train_torch(model, (cutouts[train], scaled[train], labels[train], cutouts[validation], scaled[validation], labels[validation]), device, SEED, args.epochs)
        val_p = probability(model, cutouts[validation], scaled[validation], device)
        threshold = choose_threshold(labels[validation], val_p)
        test_p = probability(model, cutouts[test], scaled[test], device)
        entry = {"parameters": int(sum(p.numel() for p in model.parameters())), "training_s": float(train_s),
                 "best_validation_loss": float(best_loss), "inference_ms_per_candidate": timed_inference(model, cutouts[test], scaled[test], device),
                 "process_rss_delta_mb": float((process.memory_info().rss-rss_before)/1024**2), "test": metrics(labels[test], test_p, threshold)}
        if device.type == "cuda": entry.update({"gpu_name": torch.cuda.get_device_name(0),
                                                  "gpu_peak_allocated_mb": float(torch.cuda.max_memory_allocated()/1024**2),
                                                  "gpu_peak_reserved_mb": float(torch.cuda.max_memory_reserved()/1024**2)})
        result["models"][label] = entry
    result["environment"] = {"torch": torch.__version__, "device": str(device), "cuda": torch.version.cuda if device.type == "cuda" else None}
    (args.output_dir / "transformer_candidate_ablation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = ["# Simulation candidate-classifier architecture ablation", "", "This controlled ablation does not alter WPDC candidate generation, ePSF fitting, deblending, or catalogue calibration.", "",
             "| Model | Parameters | Recall | Precision | F1 | AUROC | AUPRC | Threshold | Train / s | Inference / ms candidate |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, entry in result["models"].items():
        m = entry["test"]; params = "N/A" if entry["parameters"] is None else f"{entry['parameters']:,}"
        lines.append(f"| {name} | {params} | {m['recall']:.4f} | {m['precision']:.4f} | {m['f1']:.4f} | {m['auroc']:.4f} | {m['auprc']:.4f} | {m['threshold']:.4f} | {entry['training_s']:.2f} | {entry['inference_ms_per_candidate']:.4f} |")
    lines += ["", "Thresholds are chosen only on the validation partition as the highest value retaining at least 90% validation recall. The table reports the untouched test partition."]
    (args.output_dir / "README.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
