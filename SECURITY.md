# Security policy

## Supported versions

No production-supported release exists. Security fixes are applied to the
current default branch and the latest `v0.1.x` research release.

| Version | Security updates |
| --- | --- |
| `0.1.x` | Yes |
| Earlier development snapshots | No |

## Reporting a vulnerability

Do not open a public issue for a suspected credential exposure, authorization
bypass, command-injection path, Kubernetes privilege escalation, or denial of
service. Contact the repository owner privately through the security advisory
feature or another private channel listed on the owner's GitHub profile.

Include the affected commit, configuration, reproduction steps, impact, and
any suggested mitigation. Do not include real API keys, kubeconfigs, database
files, private registry credentials, cluster addresses, or third-party data.

## Prototype security boundary

- The runtime is a research prototype, not a production service.
- The API uses a shared key and does not provide user identity, RBAC, key
  rotation, rate limiting at the ingress, or tenant isolation.
- The reference Service is cluster-internal; operators should not expose it
  directly to the Internet.
- Kubernetes RBAC and workload profiles must be reviewed for each deployment.
- SQLite/PVC contents can contain task metadata and should not be published.
- GNPy validates a model and the runtime does not configure physical devices.

Before a public deployment, add identity-aware authentication, per-tenant
authorization, secret rotation, audit retention, network policy, admission
policy, resource quotas, dependency scanning, image signing, and a documented
incident-response process.
