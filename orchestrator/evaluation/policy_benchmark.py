"""Deterministic synthetic benchmark for compute-optical policies."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from math import ceil
from statistics import fmean
from typing import Iterable

from orchestrator.models.spectrum import SpectrumSlot
from orchestrator.state.optical_resource_manager import (
    OpticalResourceManager,
    OpticalResourceManagerError,
)



@dataclass(frozen=True)
class BenchmarkScenario:
    name: str
    cpu_capacity_scale: float
    memory_capacity_scale: float
    spectrum_lower_index: int
    spectrum_upper_index: int
    holding_time_scale: float

    def __post_init__(self) -> None:
        if self.cpu_capacity_scale <= 0:
            raise ValueError("cpu_capacity_scale must be positive.")
        if self.memory_capacity_scale <= 0:
            raise ValueError("memory_capacity_scale must be positive.")
        if self.spectrum_lower_index >= self.spectrum_upper_index:
            raise ValueError("Invalid scenario spectrum range.")
        if self.holding_time_scale <= 0:
            raise ValueError("holding_time_scale must be positive.")


BENCHMARK_SCENARIOS = {
    "balanced": BenchmarkScenario(
        name="balanced",
        cpu_capacity_scale=1.0,
        memory_capacity_scale=1.0,
        spectrum_lower_index=-80,
        spectrum_upper_index=80,
        holding_time_scale=1.0,
    ),
    "compute_stress": BenchmarkScenario(
        name="compute_stress",
        cpu_capacity_scale=0.65,
        memory_capacity_scale=0.75,
        spectrum_lower_index=-80,
        spectrum_upper_index=80,
        holding_time_scale=1.0,
    ),
    "spectrum_stress": BenchmarkScenario(
        name="spectrum_stress",
        cpu_capacity_scale=1.20,
        memory_capacity_scale=1.20,
        spectrum_lower_index=-40,
        spectrum_upper_index=40,
        holding_time_scale=1.50,
    ),
}


@dataclass(frozen=True)
class SyntheticTask:
    task_id: str
    cpu_m: int
    memory_mib: int
    gpu: int
    spectrum_m: int
    holding_steps: int


@dataclass
class SyntheticSite:
    site_id: str
    cpu_total_m: int
    memory_total_mib: int
    gpu_total: int
    cpu_available_m: int
    memory_available_mib: int
    gpu_available: int
    route: tuple[str, ...]
    gsnr_db: float

    def can_host(self, task: SyntheticTask) -> bool:
        return (
            self.cpu_available_m >= task.cpu_m
            and self.memory_available_mib >= task.memory_mib
            and self.gpu_available >= task.gpu
        )

    def score_after(self, task: SyntheticTask) -> float:
        cpu = (
            self.cpu_available_m - task.cpu_m
        ) / self.cpu_total_m
        memory = (
            self.memory_available_mib - task.memory_mib
        ) / self.memory_total_mib
        gpu = (
            1.0
            if task.gpu == 0
            else (
                self.gpu_available - task.gpu
            ) / max(self.gpu_total, 1)
        )

        return 0.50 * cpu + 0.35 * memory + 0.15 * gpu

    def reserve(self, task: SyntheticTask) -> None:
        self.cpu_available_m -= task.cpu_m
        self.memory_available_mib -= task.memory_mib
        self.gpu_available -= task.gpu

    def release(self, task: SyntheticTask) -> None:
        self.cpu_available_m += task.cpu_m
        self.memory_available_mib += task.memory_mib
        self.gpu_available += task.gpu


@dataclass(frozen=True)
class ActiveAllocation:
    task: SyntheticTask
    site_id: str
    expires_at: int


@dataclass(frozen=True)
class BenchmarkResult:
    scenario: str
    policy: str
    offered: int
    accepted: int
    compute_blocked: int
    optical_blocked: int
    acceptance_rate: float
    mean_cpu_utilization: float
    mean_spectrum_utilization: float
    mean_free_spectrum_runs: float

    def to_dict(self) -> dict[str, str | int | float]:
        return asdict(self)


def generate_workload(
    task_count: int,
    seed: int,
) -> list[SyntheticTask]:
    """Generate one deterministic mixed CPU/GPU workload trace."""

    if task_count <= 0:
        raise ValueError("task_count must be positive.")

    generator = random.Random(seed)
    tasks: list[SyntheticTask] = []

    for index in range(task_count):
        gpu = 1 if generator.random() < 0.12 else 0

        tasks.append(
            SyntheticTask(
                task_id=f"benchmark-{seed}-{index:06d}",
                cpu_m=generator.choice((500, 1000, 2000, 4000)),
                memory_mib=generator.choice((256, 512, 1024, 2048)),
                gpu=gpu,
                spectrum_m=generator.choice((2, 4, 6, 8)),
                holding_steps=generator.randint(4, 20),
            )
        )

    return tasks


def _new_sites(
    scenario: BenchmarkScenario,
) -> dict[str, SyntheticSite]:
    definitions = (
        (
            "site-vannes",
            12000,
            8192,
            1,
            ("Lorient-Vannes",),
            24.78,
        ),
        (
            "site-rennes",
            12000,
            8192,
            2,
            (
                "Lorient-Vannes",
                "Vannes-Ploermel",
                "Ploermel-Rennes",
            ),
            21.73,
        ),
        (
            "site-brest",
            12000,
            8192,
            0,
            ("Lorient-Quimper", "Quimper-Brest"),
            23.10,
        ),
    )

    return {
        site_id: SyntheticSite(
            site_id=site_id,
            cpu_total_m=int(cpu * scenario.cpu_capacity_scale),
            memory_total_mib=int(
                memory * scenario.memory_capacity_scale
            ),
            gpu_total=gpu,
            cpu_available_m=int(
                cpu * scenario.cpu_capacity_scale
            ),
            memory_available_mib=int(
                memory * scenario.memory_capacity_scale
            ),
            gpu_available=gpu,
            route=route,
            gsnr_db=gsnr,
        )
        for site_id, cpu, memory, gpu, route, gsnr in definitions
    }


def _first_fit(
    manager: OpticalResourceManager,
    site: SyntheticSite,
    task: SyntheticTask,
) -> SpectrumSlot | None:
    try:
        return manager.find_first_fit(
            links=site.route,
            m=task.spectrum_m,
        )
    except OpticalResourceManagerError:
        return None


def _joint_score(site: SyntheticSite, task: SyntheticTask) -> float:
    compute_score = site.score_after(task)
    optical_score = max(min(site.gsnr_db / 30.0, 1.0), 0.0)
    return 0.75 * compute_score + 0.25 * optical_score


def _select(
    policy: str,
    task: SyntheticTask,
    sites: dict[str, SyntheticSite],
    manager: OpticalResourceManager,
    generator: random.Random,
) -> tuple[SyntheticSite | None, SpectrumSlot | None, str | None]:
    compute_feasible = [
        site for site in sites.values() if site.can_host(task)
    ]

    if policy == "compute_first":
        if not compute_feasible:
            return None, None, "compute"

        site = max(
            compute_feasible,
            key=lambda item: item.score_after(task),
        )
        slot = _first_fit(manager, site, task)
        return site, slot, None if slot is not None else "optical"

    optical_feasible = [
        (site, slot)
        for site in sites.values()
        if (slot := _first_fit(manager, site, task)) is not None
    ]

    if policy == "optical_first":
        if not optical_feasible:
            return None, None, "optical"

        site, slot = max(
            optical_feasible,
            key=lambda item: item[0].gsnr_db,
        )
        if not site.can_host(task):
            return None, None, "compute"
        return site, slot, None

    jointly_feasible = [
        (site, slot)
        for site, slot in optical_feasible
        if site.can_host(task)
    ]

    if not jointly_feasible:
        reason = "compute" if not compute_feasible else "optical"
        return None, None, reason

    if policy == "joint_weighted":
        site, slot = max(
            jointly_feasible,
            key=lambda item: _joint_score(item[0], task),
        )
        return site, slot, None

    if policy == "random_feasible":
        site, slot = generator.choice(jointly_feasible)
        return site, slot, None

    raise ValueError(f"Unsupported policy: {policy}")


def _spectrum_samples(
    manager: OpticalResourceManager,
    links: tuple[str, ...],
) -> tuple[float, float]:
    lower = manager.spectrum_lower_index
    upper = manager.spectrum_upper_index
    capacity = (upper - lower) * len(links)
    occupied = 0
    free_runs = 0

    allocations = manager.active_allocations()

    for link in links:
        occupancy = [False] * (upper - lower)

        for allocation in allocations:
            if link not in allocation.links:
                continue

            start = max(allocation.slot.lower_index, lower) - lower
            stop = min(allocation.slot.upper_index, upper) - lower

            for index in range(start, stop):
                occupancy[index] = True

        occupied += sum(occupancy)
        previous_occupied = True

        for is_occupied in occupancy:
            if not is_occupied and previous_occupied:
                free_runs += 1
            previous_occupied = is_occupied

    return occupied / max(capacity, 1), free_runs / len(links)


def run_policy(
    tasks: Iterable[SyntheticTask],
    policy: str,
    seed: int,
    scenario: BenchmarkScenario = BENCHMARK_SCENARIOS["balanced"],
) -> BenchmarkResult:
    """Run one scheduling policy over a fixed workload trace."""

    workload = list(tasks)
    sites = _new_sites(scenario)
    manager = OpticalResourceManager(
        spectrum_lower_index=scenario.spectrum_lower_index,
        spectrum_upper_index=scenario.spectrum_upper_index,
    )
    generator = random.Random(seed + 10000)
    active: list[ActiveAllocation] = []

    accepted = 0
    compute_blocked = 0
    optical_blocked = 0
    cpu_samples: list[float] = []
    spectrum_samples: list[float] = []
    fragmentation_samples: list[float] = []

    all_links = tuple(
        sorted({link for site in sites.values() for link in site.route})
    )

    for step, task in enumerate(workload):
        expired = [
            allocation
            for allocation in active
            if allocation.expires_at <= step
        ]

        for allocation in expired:
            sites[allocation.site_id].release(allocation.task)
            manager.release(allocation.task.task_id)
            active.remove(allocation)

        site, slot, blocked_reason = _select(
            policy=policy,
            task=task,
            sites=sites,
            manager=manager,
            generator=generator,
        )

        if site is None or slot is None:
            if blocked_reason == "compute":
                compute_blocked += 1
            else:
                optical_blocked += 1
        else:
            site.reserve(task)
            manager.reserve(
                task_id=task.task_id,
                links=site.route,
                slot=slot,
            )
            active.append(
                ActiveAllocation(
                    task=task,
                    site_id=site.site_id,
                    expires_at=(
                        step
                        + max(
                            1,
                            ceil(
                                task.holding_steps
                                * scenario.holding_time_scale
                            ),
                        )
                    ),
                )
            )
            accepted += 1

        total_cpu = sum(site.cpu_total_m for site in sites.values())
        available_cpu = sum(
            site.cpu_available_m for site in sites.values()
        )
        cpu_samples.append((total_cpu - available_cpu) / total_cpu)

        spectrum, fragmentation = _spectrum_samples(
            manager,
            all_links,
        )
        spectrum_samples.append(spectrum)
        fragmentation_samples.append(fragmentation)

    offered = len(workload)
    return BenchmarkResult(
        scenario=scenario.name,
        policy=policy,
        offered=offered,
        accepted=accepted,
        compute_blocked=compute_blocked,
        optical_blocked=optical_blocked,
        acceptance_rate=accepted / offered,
        mean_cpu_utilization=fmean(cpu_samples),
        mean_spectrum_utilization=fmean(spectrum_samples),
        mean_free_spectrum_runs=fmean(fragmentation_samples),
    )


def run_benchmark(
    task_count: int = 1000,
    seeds: tuple[int, ...] = (7, 17, 29, 43, 61),
    scenario: BenchmarkScenario = BENCHMARK_SCENARIOS["balanced"],
) -> list[BenchmarkResult]:
    """Average policy results over deterministic workload seeds."""

    if not seeds:
        raise ValueError("At least one seed is required.")

    policies = (
        "joint_weighted",
        "compute_first",
        "optical_first",
        "random_feasible",
    )
    by_policy: dict[str, list[BenchmarkResult]] = {
        policy: [] for policy in policies
    }

    for seed in seeds:
        tasks = generate_workload(task_count, seed)

        for policy in policies:
            by_policy[policy].append(
                run_policy(
                    tasks,
                    policy,
                    seed,
                    scenario=scenario,
                )
            )

    results: list[BenchmarkResult] = []

    for policy in policies:
        samples = by_policy[policy]
        results.append(
            BenchmarkResult(
                scenario=scenario.name,
                policy=policy,
                offered=task_count * len(seeds),
                accepted=sum(item.accepted for item in samples),
                compute_blocked=sum(
                    item.compute_blocked for item in samples
                ),
                optical_blocked=sum(
                    item.optical_blocked for item in samples
                ),
                acceptance_rate=fmean(
                    item.acceptance_rate for item in samples
                ),
                mean_cpu_utilization=fmean(
                    item.mean_cpu_utilization for item in samples
                ),
                mean_spectrum_utilization=fmean(
                    item.mean_spectrum_utilization for item in samples
                ),
                mean_free_spectrum_runs=fmean(
                    item.mean_free_spectrum_runs for item in samples
                ),
            )
        )

    return results
