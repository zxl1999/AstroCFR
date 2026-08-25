# CNN CPU/GPU resource benchmark

The original `_StarBogusNet` implementation is imported unchanged from the CSST working directory. The benchmark uses the exact 25 x 25 normalized image cutout, 17 handcrafted features, 28,081 trainable parameters, 30 training epochs, training batch size 64, inference batch size 256, and fixed seed 42. Inputs are deterministic representative tensors; this is a resource-only measurement and does not claim an accuracy result.

| Device | Training / s | Training / epoch s | Inference / ms per 1,000 candidates | Process RSS delta / MB | GPU peak allocated / MB | GPU peak reserved / MB |
|---|---:|---:|---:|---:|---:|---:|
| CPU | 8.738 | 0.291 | 45.285 | 147.7 | N/A | N/A |
| NVIDIA RTX 5060 Laptop GPU | 3.674 | 0.122 | 8.984 | 655.0 | 58.3 | 86.0 |

The GPU run is approximately 2.4 times faster for training and 5.0 times faster for model-stage inference. These values exclude candidate generation, cutout construction, disk I/O, and catalogue calibration; they must not be compared directly with the end-to-end HST CPU pipeline timings.

Machine-readable output: `results/cnn_cpu_gpu_resource_benchmark/cnn_cpu_gpu_resource_benchmark.json`.
