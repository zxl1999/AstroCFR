# Lightweight Transformer candidate-classifier ablation

The ablation keeps WPDC candidate generation, background subtraction, source association, and all ePSF/deblending stages unchanged. It only replaces the candidate-quality classifier on the four CSST-like simulation chips (12, 13, 17, and 18).

## Reproducible protocol

- Candidates within 2 pixels of the nearest simulated reference are positive; candidates beyond 8 pixels are negative. The 2--8 pixel annulus is excluded.
- The resulting 11,615 candidates contain 3,615 positives and 8,000 negatives.
- A stratified 60/20/20 train/validation/test split (seed `20260806`) is used. The validation partition selects the highest threshold retaining at least 90% validation recall; all reported values are from the untouched test partition.
- Every model receives the same normalized 25 x 25 single-channel cutout and the same 17 WPDC handcrafted features.
- The Transformer has 5 x 5 non-overlapping patches (25 tokens), 64-dimensional embeddings, two four-head encoder layers, and a 17-feature MLP branch (74,193 trainable parameters).

## Results

The machine-readable output is `results/transformer_candidate_ablation/transformer_candidate_ablation.json`; the candidate cache is `simulation_candidate_cutouts.npz`.

| Model | Recall | Precision | F1 | AUROC | AUPRC | Train / s | Inference / ms per candidate |
|---|---:|---:|---:|---:|---:|---:|---:|
| RandomForest | 0.9585 | 0.9943 | 0.9761 | 0.9994 | 0.9986 | 0.68 | 0.0370 |
| Original WPDC CNN | 0.9170 | 0.9881 | 0.9512 | 0.9978 | 0.9921 | 16.48 | 0.0062 |
| Lightweight patch Transformer | 0.9336 | 0.9926 | 0.9622 | 0.9987 | 0.9958 | 31.61 | 0.0067 |

The CNN and Transformer runs used the NVIDIA GeForce RTX 5060 Laptop GPU (PyTorch `2.10.0.dev20250926+cu128`, CUDA 12.8). Peak allocated/reserved GPU memory was 58.3/86.0 MB for the CNN and 33.2/56.0 MB for the Transformer. These values are classifier-stage measurements, not end-to-end survey throughput.

The Transformer improves over the original CNN in this split, but does not exceed the RF branch. It is therefore retained as a reproducible architecture ablation and does not change the paper's bounded system-level claim or motivate a GAN/diffusion/Transformer replacement of WPDC.
