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
- `tui.py` — Textual TUI dashboard (two-pane, keyboard-driven) for VM list, actions, templates, and capsules
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

- **Defaults over configuration**: templates encode decisions; the CLI is for exceptions.
- **Secure by default**: network off, SELinux on, no guest agent. Explicit opt-in for connectivity.
- **Tiered interaction**: `create` is 2 prompts, `--advanced` adds resources, `--full` exposes everything.
- **One-shot runner**: `latita run` for ephemeral, auto-cleaned VMs with no persistent state.
- **Project config**: `.latita` file in cwd merged with CLI flags, similar to a `Smolfile`.

### System vs Session mode

| | `qemu:///system` | `qemu:///session` |
|---|---|---|
| **Privileges** | Root / sudo required for network setup | Unprivileged — works out of the box |
| **Networking** | Shared NAT bridges (e.g. `default` at 192.168.122.0/24). VMs on the same network can talk to each other. | Isolated SLIRP per VM (10.0.2.0/24). VMs **cannot** reach each other. |
| **SSH access** | Direct VM IP (e.g. 192.168.122.235) | `localhost:PORT` via QEMU `hostfwd` (port 2222-9999) |
| **Use case** | Production, multi-VM labs, networking tests | Quick one-offs, CI, unprivileged workstations |
| **Test suite** | `test_desktop_minimal_reaches_code_server` (MVP E2E) requires system mode | All other E2E tests run in session mode |

**Session mode specifics**: When `LIBVIRT_DEFAULT_URI` is `qemu:///session` (or auto-detected as fallback), latita:
- Skips root-only network setup (no bridge creation)
- Forces `user` networking (SLIRP) even if the template asks for `nat`
- Skips `setfacl` / `grant_qemu_path_access`
- Injects `--qemu-commandline` with `hostfwd=tcp::PORT-:22` so SSH works via localhost
- The forwarded port is stored in instance env as `FORWARDED_SSH_PORT`

**System mode specifics**:
- Requires `libvirtd` running and the user in the `libvirt` group (or sudo)
- Creates/activates the `default` NAT network for shared VM-to-VM routing
- Uses the VM's actual DHCP-assigned IP for SSH
- **Multi-VM E2E tests** (desktop → headless code-server) only work in system mode because session-mode VMs live on isolated SLIRP networks

**Auto-detection**: `Config.default()` probes `qemu:///system` at startup. If the socket is absent or connection is refused, it automatically falls back to `qemu:///session`. Set `LIBVIRT_DEFAULT_URI` explicitly to override.

**Libvirt connectivity check**: Before any VM operation, latita verifies the configured libvirt URI is reachable. If `qemu:///system` is unavailable, `create_instance` fails with: `Cannot connect to libvirt at qemu:///system. Set LIBVIRT_DEFAULT_URI=qemu:///session to use user-level libvirt without sudo, or ensure the system libvirtd daemon is running and sudo is configured.`

**virt-install and system gi**: `virt-install` depends on `gi` (PyGObject) which is typically installed at the system Python level. When latita runs under `uv run`, the venv's site-packages takes precedence over system site-packages, so `virt-install` would fail to find `gi`. To fix this, `virt_install()` in `libvirt.py` detects the system site-packages path (by probing `/usr/bin/python3`) and injects it via `PYTHONPATH` when calling virt-install. This ensures virt-install works correctly regardless of whether it's invoked via `uv run` or directly.

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

### Base image catalog maintenance

`BASE_IMAGES` in `config.py` stores the curated list of downloadable base images. **Fedora entries use directory URLs with `discover: True`** — the latest point release is scraped from the directory listing at download time, so point-release churn (e.g., `1.6` → `1.7`) never breaks downloads or tests.

When a **new Fedora major release** drops (e.g., Fedora 44):
1. Verify Cloud images exist at `releases/N/Cloud/x86_64/images/` (not just the release root).
2. Add the entry to `BASE_IMAGES` with `discover: True` and a directory URL.
3. Update `Config.default()`'s `default_base_name` if adopting the new release as default.
4. Do **not** auto-detect the latest major release at runtime — new releases may lack Cloud images for weeks, or the image format may change.

Ubuntu LTS entries (e.g., `noble/current/`) use a stable `current/` symlink and do not need discovery.

**Mirror fallbacks**: Fedora entries include `mirror_urls` (e.g., `mirrors.kernel.org`). `_download_base` tries the primary redirector first, then falls back to mirrors if the connection fails (e.g., FCIX mirror `edgeuno-bod2.mm.fcix.net` returning 443 errors). Each attempt uses `curl --retry 3 --connect-timeout 30 --max-time 600` for resilience.

### Future work

- **Snapshot / clone support**: `latita snapshot <name>` and `latita clone <name> <new-name>` using `qemu-img` backing chains.
- **Template marketplace**: Share templates via a Git-based registry or simple HTTP index.
