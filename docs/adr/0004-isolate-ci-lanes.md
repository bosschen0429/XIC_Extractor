---
status: accepted
---

# Isolate CI lanes by what they compete for

A fresh pytest process is not a fresh Windows machine: Chromium still shares
the VM’s socket and process resources with scientific and packaging tests.
Use disposable GitHub-hosted workers for PR code, including installation.
Run Chromium on separate VMs, one disjoint shard per VM, with the locked
Playwright headless shell. The browser lane runs in full when relevant; an
empty browser selection is not success. Keep DOM snapshots as failure evidence
without the extra rendering work of screencast frames. Scale browser watchdog
deadlines for a slow CI machine; they are not latency requirements.

Let xdist set ordinary test parallelism rather than multiplying its workers by
native BLAS thread pools. Run bounded serial subprocess/build tests outside
that pool and the architecture census once in lint. Only the coverage plan
blocks test allocation; lint runs independently. A failed plan cannot authorize
a skip, and a new branch with no comparison range requires full coverage.

Distinguish “all files” from “no selected files”: passing no paths to pytest
runs everything. For an explicit subset, a marker may select no tests on an
ordinary platform lane (pytest exit 5); that case is not a passing test.
The [testing guide](../agent/testing.md) owns execution instructions.
