# Sandbox Security Hardening Backlog

Status: **Open — deferred for the current controlled smoke test**
Last reviewed: 2026-11-10

This document records security work intentionally deferred while validating the current human-approval and sandbox execution path. The current Docker sandbox is defense-in-depth, not a guarantee against container escape or host compromise. Do not describe it as fully audited or production-hardened.

## Current baseline

The current runner uses fixed Python/Node runtime images pinned by digest, a fixed Docker invocation, no network, read-only root filesystem, non-root UID/GID, all Linux capabilities dropped, `no-new-privileges`, resource/process/file-descriptor limits, bounded input/output and wall-clock timeout. The runner uses a host lock and the privileged broker consumes a single approved source snapshot after checking its SHA-256 and claiming it once. The broker communicates with the unprivileged Orchestrator through a permission-restricted Unix socket.

These controls reduce risk but do not eliminate vulnerabilities in the host kernel, Docker daemon/runtime, broker, or the sandbox boundary.

## Deferred security work — must be completed before general/untrusted-code production use

- [ ] **Docker/AppArmor verification:** inspect the actual running container security options and confirm the expected Docker default seccomp and AppArmor profiles are applied; document behavior when AppArmor is unavailable.
- [ ] **User namespace isolation:** evaluate Docker user-namespace remapping or rootless execution on DGX Spark ARM64; verify UID/GID mapping and compatibility before enabling.
- [ ] **Broker security review:** audit the complete broker source and systemd unit; validate every input, UUID, DB state transition, socket peer permissions, response framing/size, timeout, subprocess invocation, and error path. Keep Docker socket and runner invocation inaccessible to the Orchestrator.
- [ ] **Host exposure tests:** perform controlled negative tests proving the sandbox cannot read sensitive host paths, access Docker/container sockets, signal host processes, or reach host/LAN/internet services. Never use destructive payloads.
- [ ] **Resource exhaustion tests:** validate memory, swap, CPU, PID, file-descriptor, disk/tmpfs, output and wall-clock limits; verify cleanup after timeout, output-limit and broker/runner failure.
- [ ] **Concurrency and replay tests:** verify the global lock, single-use approval claim, expiration, duplicate requests, concurrent broker requests and crash/restart behavior.
- [ ] **Approval integrity tests:** prove that any source/digest mismatch, expired/rejected/pending approval, wrong execution/task association, or replay is rejected before execution. Ensure the exact displayed source is the source executed.
- [ ] **Image and supply-chain maintenance:** document image provenance, digest refresh procedure, vulnerability scanning/update policy and offline update process. Keep runtime images pinned by digest.
- [ ] **Kernel/runtime maintenance:** define a process for tracking and applying DGX host kernel, Docker Engine and runtime security updates.
- [ ] **Operational safeguards:** add auditable execution records, bounded retention, clear user-visible risk warnings, and a kill/disable procedure that does not require exposing Docker control to the Orchestrator.
- [ ] **IDE workspace build isolation:** the reference adapter's `.NET build/test` commands execute project-controlled MSBuild targets and tests directly as the workstation user. Design a multi-file, offline, no-host-network sandbox with explicit workspace and package-cache mounts; do not route these commands through the single-file source runner.
- [ ] **Isolation alternatives:** evaluate gVisor and/or a MicroVM boundary for stronger isolation, with ARM64 compatibility, performance and operational costs measured.

## Release gate

Until the relevant items above are reviewed and tested, restrict execution to explicitly approved, reviewed, low-risk smoke tests. Do not enable automatic execution of model-generated code, broad host mounts, network access, arbitrary commands, package installation, or privileged execution inside the sandbox.

A successful sample execution is a functional test only; it is not evidence that the sandbox resists malicious code or container escape.
