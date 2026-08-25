# Environment capture

The controlled HST/ACS and CSST scientific benchmarks were run on Windows 11 with 64-bit Python 3.12.7 (Anaconda). Their direct Python dependencies are pinned in `requirements-lock.txt`. These main benchmark paths are CPU-only, so GPU memory is not applicable to their comparisons. Record the CPU model, thread variables, and operating-system build for every rerun.

The separate StarBogusNet CNN resource experiment in Table 23 also used an NVIDIA GeForce RTX 5060 Laptop GPU and the observed development build `torch 2.10.0.dev20250926+cu128`. This time-stamped nightly build is documented in `../supplementary/requirements-cpu-gpu-tested.txt` and `../supplementary/environment_cpu_gpu.yml`; it must not be interpreted as a package guaranteed to resolve from stable Conda channels. Reproduce that row with a matching CUDA 12.8 PyTorch nightly build, or record the replacement PyTorch/CUDA build and treat the result as a new hardware measurement.

The machine-readable benchmark output is `../results/cnn_cpu_gpu_resource_benchmark/cnn_cpu_gpu_resource_benchmark.json`. Package locks establish the software environment; they do not make runtimes directly comparable across different hardware.
