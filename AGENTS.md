# Latita agent notes

## Architecture

Latita replaces the previous `ephemctl` bash/python monolith with a clean Python package built on `libvirt`/`qemu`.

### Key modules

- `config.py` — root directory resolution, `.latita` / `.cap` YAML loaders
- `operations.py` — VM CRUD, ephemeral lifecycle enforcement, revive logic
- `cloudinit.py` — cloud-config generation; merges template + capsule fragments
- `capsules.py` — capsule registry, compatibility checks, live vs create-time application
- `hardening.py` — SELinux, no-guest-agent, nwfilter egress controls
- `libvirt.py` — thin wrapper around `virsh` and `virt-install`
- `metadata.py` — JSON-based instance state (recipe, spec, env)
- `cli.py` — Typer CLI with subcommands for VMs, capsules, and templates

### File formats

Templates live in `<root>/templates/*.latita` as YAML.
Capsules live in `<root>/capsules/*.cap` as YAML.

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

### Tests

Run `python -m py_compile src/latita/*.py` for a quick smoke check.
