# Latita agent notes

## Architecture

Latita replaces the previous `ephemctl` bash/python monolith with a clean Python package built on `libvirt`/`qemu`.

### Key modules

- `config.py` — root directory resolution, `.latita` / `.cap` YAML loaders, project-level `.latita` config
- `operations.py` — VM CRUD, ephemeral lifecycle enforcement, revive logic, one-shot `run_instance`
- `cloudinit.py` — cloud-config generation; merges template + capsule fragments
- `capsules.py` — capsule registry, compatibility checks, `depends_on` dependency resolution, live vs create-time application
- `hardening.py` — SELinux, no-guest-agent, nwfilter egress controls
- `libvirt.py` — thin wrapper around `virsh` and `virt-install`
- `metadata.py` — JSON-based instance state (recipe, spec, env)
- `cli.py` — Typer CLI with subcommands for VMs, capsules, templates, and the `run` one-shot runner
- `prompts.py` — tiered interactive wizards (simple, advanced, full) and template generator

### File formats

Templates live in `<root>/templates/*.latita` as YAML.
Capsules live in `<root>/capsules/*.cap` as YAML.
Project-level config lives in `.latita` (cwd) as YAML.

### Ephemeral lifecycle

Stored in `spec.json` per instance:
- `transient` — passed to virt-install
- `destroy_on_stop` — host-side shred + rm on `latita stop`
- `max_runs` / `run_count` — checked on `start`
- `expire_at` — absolute ISO timestamp checked on `start`

### Security defaults

- `selinux: true` and `no_guest_agent: true` are defaults.
- `restrict_network` applies a libvirt nwfilter; `allow_hosts` can narrow it.
- Disk overlays are shredded with `shred -n 3` on destroy.
- **Networking is isolated by default** (`mode: isolated`). VMs have no external access unless `--net` or `network: nat` is explicitly set.

### UX design principles

Inspired by [smolvm](https://github.com/smol-machines/smolvm):
- **Defaults over configuration**: templates encode decisions; the CLI is for exceptions.
- **Secure by default**: network off, SELinux on, no guest agent. Explicit opt-in for connectivity.
- **Tiered interaction**: `create` is 2 prompts, `--advanced` adds resources, `--full` exposes everything.
- **One-shot runner**: `latita run` for ephemeral, auto-cleaned VMs with no persistent state.
- **Project config**: `.latita` file in cwd merged with CLI flags, similar to a `Smolfile`.

### Session mode

If `LIBVIRT_DEFAULT_URI` is `qemu:///session` or `Config.for_tests` is used, latita:
- Skips root-only network setup (no bridge creation)
- Falls back to `user` networking (SLIRP) instead of NAT bridges
- Skips `setfacl` / `grant_qemu_path_access`
- Works out of the box for unprivileged users

**Port forwarding for SSH**: Session-mode VMs get automatic QEMU `hostfwd` port forwarding via `--qemu-commandline`. `create_instance` picks a free localhost port (e.g. 2222-9999) and forwards it to guest port 22. The port is stored in instance env as `FORWARDED_SSH_PORT`. `ssh_instance` and `apply_capsule_live` use `localhost:PORT` when this variable is set.

**Cloud-init in session mode**: Latita builds a persistent NoCloud ISO with `xorriso` (label `cidata`) and attaches it as a CD-ROM disk. This is more reliable than `virt-install --cloud-init`, whose temporary ISO was sometimes missing by the time the guest booted in `qemu:///session` mode. The test suite includes a real end-to-end SSH test (`test_real_ssh_to_vm_executes_command`) that verifies cloud-init creates the `dev` user, installs `openssh-server`, and enables `sshd`.

### Why Python (not Rust)

Latita is a CLI orchestrator, not a VMM. The actual runtime is spent waiting on:
- `virt-install` (5-30s)
- `qemu-img` operations (1-5s)
- VM boot (2-60s)
- SSH round-trips

Python adds ~100ms startup overhead to commands that take 15-60s. A Rust rewrite would cut that to ~15ms — a 0.1% improvement at the cost of ~2-4 weeks of rewrite work, new dependency chains (libvirt bindings, SSH clients), and rewriting 124 tests.

Rewrite to Rust would make sense if latita were:
- A micro-VMM (like Firecracker) needing sub-millisecond boots
- A daemon handling thousands of requests/second
- Deployed to an embedded environment without a Python runtime

For a CLI that orchestrates libvirt/QEMU, Python is the right trade-off.

### Capsule dependency system

- `depends_on: [capsule-name]` declares dependencies.
- `resolve_capsules()` does depth-first traversal, deduplicates, and orders so dependencies provision before dependents.
- Cycle detection raises a clear error.
- `code-server` depends on `podman-host`; `open-webui` depends on `ollama`.
- The `ai-agents` mega-capsule depends on `hermes` + `openclaw` and installs Claude Code, Codex, Gemini CLI, Kimi CLI, OpenCode, OpenClaw, Hermes, and Aider.

### Tests

Run `python -m py_compile src/latita/*.py` for a quick smoke check.
Run `.venv3/bin/python -m pytest tests/` for the full suite (195 tests including real VM lifecycle, capsule dependency resolution, cloud-init provision merging, end-to-end SSH to a live Fedora VM, and heavy capsule integration tests).

### Known bugs fixed

- **dnf package block `+` artifact**: `_package_install_block` in `cloudinit.py` previously joined package names with ` \\n+      `, causing literal `+` arguments to be passed to `dnf install`. This caused all multi-package installs to fail with "No match for argument: +". Fixed by removing the stray `+` characters from the join string.

### Future work

- **Snapshot / clone support**: `latita snapshot <name>` and `latita clone <name> <new-name>` using `qemu-img` backing chains.
- **Template marketplace**: Share templates via a Git-based registry or simple HTTP index.
- **TUI dashboard**: A `textual` or `rich` live view of running VMs with resource usage.
