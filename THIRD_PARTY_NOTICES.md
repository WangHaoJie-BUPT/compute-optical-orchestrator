# Third-party notices

Project-authored code is licensed under the Apache License 2.0. Third-party
components and adapted material retain their upstream licenses.

## GNPy

- Project: GNPy
- Source: https://github.com/Telecominfraproject/oopt-gnpy
- Pinned commit: `d65865b8b7d9c6343c208e1814e2e210a5b6c5ca`
- License: BSD 3-Clause
- Copyright: Copyright (c) 2018, Telecom Infra Project

GNPy is referenced as the `third_party/oopt-gnpy` Git submodule and installed
by the project Dockerfile. The files in `topologies/gnpy_demo` are copies of
GNPy's `gnpy/example-data/eqpt_config.json` and
`gnpy/example-data/meshTopologyExampleV2.xls` at the pinned commit. Their
copyright and license remain with the upstream authors.

BSD 3-Clause License

Copyright (c) 2018, Telecom Infra Project
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its contributors
  may be used to endorse or promote products derived from this software
  without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

## Direct runtime and test dependencies

The project installs, but does not modify, the following direct dependencies.
Their transitive dependencies retain their own upstream terms.

| Component | Version | License | Source |
| --- | --- | --- | --- |
| FastAPI | 0.139.0 | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | 0.51.0 | BSD 3-Clause | https://github.com/encode/uvicorn |
| Prometheus Python client | 0.25.0 | Apache-2.0 AND BSD-2-Clause | https://github.com/prometheus/client_python |
| Kubernetes Python client | 36.0.2 | Apache-2.0 | https://github.com/kubernetes-client/python |
| httpx2 | 2.5.0 | BSD 3-Clause | https://github.com/pydantic/httpx2 |
| kubectl | 1.35.5 | Apache-2.0 | https://github.com/kubernetes/kubernetes |

## Operations console dependencies

The browser console installs, but does not modify, the following direct
dependencies. Their transitive dependencies retain their own upstream terms.

| Component | Version | License | Source |
| --- | --- | --- | --- |
| React | 19.2.8 | MIT | https://github.com/facebook/react |
| React DOM | 19.2.8 | MIT | https://github.com/facebook/react |
| React Flow (XYFlow) | 12.11.2 | MIT | https://github.com/xyflow/xyflow |
| Recharts | 3.10.1 | MIT | https://github.com/recharts/recharts |
| Lucide React | 1.28.0 | ISC | https://github.com/lucide-icons/lucide |
| Vite | 8.2.0 | MIT | https://github.com/vitejs/vite |
| TypeScript | 7.0.2 | Apache-2.0 | https://github.com/microsoft/TypeScript |
| Vitest | 4.1.10 | MIT | https://github.com/vitest-dev/vitest |

The reference manifests also use external container images. Those images are
not stored in this repository and remain subject to the licenses published by
their respective distributors.
