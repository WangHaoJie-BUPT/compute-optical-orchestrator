# Accelerator-aware scheduling contract

The runtime represents accelerators as Kubernetes extended resources instead
of assuming that every device is an NVIDIA GPU. The built-in discovery list is:

| Device family | Extended resource |
| --- | --- |
| NVIDIA GPU | `nvidia.com/gpu` |
| Huawei Ascend NPU | `huawei.com/Ascend910` |
| AMD GPU | `amd.com/gpu` |

Additional resource names can be supplied to `KubernetesResourceDiscovery`.
Active regular-container, init-container, and Pod requests are included in the
available-device calculation.

## Node metadata

Capability constraints use these canonical Node labels:

| Label | Meaning | Example |
| --- | --- | --- |
| `accelerator.compute-optical.io/product` | Device product | `Ascend-910B` |
| `accelerator.compute-optical.io/memory-mib` | Per-device memory in MiB | `65536` |
| `accelerator.compute-optical.io/interconnect` | Device fabric | `hccs` |
| `accelerator.compute-optical.io/partition-mode` | Sharing mode | `full` |

For NVIDIA nodes, discovery also understands
`nvidia.com/gpu.product`, `nvidia.com/gpu.memory`, and
`nvidia.com/mig.strategy` as metadata fallbacks.

Example:

```bash
kubectl label node ascend-worker \
  accelerator.compute-optical.io/product=Ascend-910B \
  accelerator.compute-optical.io/memory-mib=65536 \
  accelerator.compute-optical.io/interconnect=hccs \
  accelerator.compute-optical.io/partition-mode=full
```

The device plugin remains responsible for advertising the extended resource
in Node `status.allocatable`. Labels alone never create accelerator capacity.

## Scheduling sequence

1. Resource discovery subtracts active Pod requests from each extended
   resource advertised by a Node.
2. The planner rejects insufficient counts or mismatched product, minimum
   memory, interconnect, and partition mode before invoking GNPy.
3. Feasible candidates include their complete accelerator inventory in the
   structured candidate log and blocked-task audit event.
4. The selected Job requests the exact extended resource and repeats the
   capability constraints as required Node affinity.
5. The existing exact-hostname selector prevents Kubernetes from silently
   moving the workload to a different accelerator node.

## Scope

This contract models node-level accelerator capabilities. It does not yet
model PCIe/NUMA locality, an individual NVLink/HCCS fabric graph, time slicing,
MIG slice geometry, gang scheduling, or multi-node collective placement. Those
features require device-plugin data and a topology-aware scheduler extension,
not labels alone.
