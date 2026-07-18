# Synthetic scheduling-policy baseline

Status: deterministic algorithm experiment, not a Kubernetes or GNPy latency
benchmark.

## Method

The experiment generates five workload traces with 1,000 tasks per trace.
Each trace uses a fixed seed and mixes CPU, memory, GPU, spectrum-width, and
holding-time demands. Every policy sees the same tasks in the same order.

```bash
python scripts/benchmark_scheduling_policies.py \
  --tasks 1000 \
  --seeds 7 17 29 43 61 \
  --scenarios balanced compute_stress spectrum_stress
```

The synthetic topology contains three compute sites and five fiber links.
Vannes and Rennes share part of their route; Brest uses a disjoint route.
Spectrum allocation uses the project's `OpticalResourceManager` and
First-Fit implementation.

Three scenarios isolate different constraints:

- `balanced`: reference compute and spectrum capacity;
- `compute_stress`: CPU capacity is 65% and memory capacity is 75%;
- `spectrum_stress`: compute capacity is 120%, spectrum width is halved,
  and holding times are multiplied by 1.5.

## Baseline results

Measured on 2026-07-17 from the upgrade working tree based on `2dad40d`:

| Scenario | Policy | Acceptance | Compute blocked | Optical blocked | CPU util. | Spectrum util. | Free runs |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Balanced | Joint weighted | 0.975 | 123 | 0 | 0.590 | 0.292 | 2.704 |
| Balanced | Compute first | 0.973 | 129 | 4 | 0.588 | 0.294 | 2.724 |
| Balanced | Optical first | 0.578 | 2112 | 0 | 0.273 | 0.088 | 1.345 |
| Balanced | Random feasible | 0.974 | 128 | 1 | 0.589 | 0.297 | 2.733 |
| Compute stress | Joint weighted | 0.844 | 780 | 0 | 0.668 | 0.255 | 2.465 |
| Compute stress | Compute first | 0.843 | 783 | 0 | 0.668 | 0.256 | 2.494 |
| Compute stress | Optical first | 0.432 | 2841 | 0 | 0.265 | 0.066 | 1.265 |
| Compute stress | Random feasible | 0.849 | 756 | 1 | 0.679 | 0.257 | 2.479 |
| Spectrum stress | Joint weighted | 0.765 | 71 | 1106 | 0.577 | 0.626 | 2.427 |
| Spectrum stress | Compute first | 0.676 | 73 | 1546 | 0.524 | 0.537 | 2.438 |
| Spectrum stress | Optical first | 0.638 | 1304 | 505 | 0.424 | 0.418 | 1.506 |
| Spectrum stress | Random feasible | 0.770 | 76 | 1075 | 0.578 | 0.635 | 2.407 |

## Interpretation and limits

The balanced trace is primarily compute-constrained. Joint weighted placement
slightly improves acceptance over compute-first, while optical-first placement
overloads its preferred compute site. Under spectrum stress, joint placement
improves acceptance by 8.9 percentage points over compute-first because it can
avoid a compute-attractive but spectrum-congested site.

Random-feasible placement is 0.5 percentage points higher than the current
joint score in both compute-stress and spectrum-stress results. The benchmark
therefore does not support claiming that the fixed 75/25 score is optimal. It
motivates adaptive weights, fragmentation-aware scoring, and confidence
analysis over a wider parameter sweep.

Before using benchmark numbers in a paper or resume, add confidence intervals
and separately measure real GNPy planning latency and Kubernetes convergence
time. Synthetic acceptance results should remain labelled as simulation.
