# Latita - Ephemeral libvirt/QEMU lab manager with capsules

Spin up isolated VMs in seconds, harden them automatically, and extend them with drop-in capsules.

## Install

```bash
uv tool install git+https://github.com/yourname/latita
```

Or clone and run locally:

```bash
uv sync
uv run latita --help
```

## Quick start

```bash
# One-time host setup
latita bootstrap

# Create a headless ephemeral VM
latita create headless --name myvm

# Create a desktop VM
latita create desktop --name mydesktop

# List VMs
latita list

# SSH into a VM
latita ssh myvm

# Apply a capsule to a running VM
latita capsule apply myvm code-server

# Stop (ephemeral VMs are destroyed automatically)
latita stop myvm
```

## Templates

Templates are YAML files with the `.latita` extension. They define the base VM shape.

```yaml
# templates/headless.latita
profile: headless
os_family: fedora
description: Minimal headless dev box
base_image: fedora43-base.qcow2
cpus: 2
memory: 4096
disk_size: 20G
guest_user: dev
passwordless_sudo: true

ephemeral:
  transient: true
  destroy_on_stop: false

network:
  mode: nat
  nat_network: default
  mgmt_ip: 10.31.0.10

security:
  selinux: true
  no_guest_agent: true
  restrict_network: false

provision:
  packages:
    - git
    - vim
```

Place custom templates in `~/<root>/templates/`.

## Capsules

Capsules are YAML files with the `.cap` extension. They extend VMs at creation time (cloud-init) or live via SSH.

```yaml
# capsules/code-server.cap
description: Run code-server via podman
compatibility:
  profiles: [headless, desktop]
  os_family: [fedora, ubuntu]

provision:
  packages: [podman]
  user_commands:
    - podman run -d --name code-server -p 127.0.0.1:8443:8080 ghcr.io/coder/code-server:latest

live:
  user: dev
  commands:
    - podman rm -f code-server 2>/dev/null || true
    - podman run -d --name code-server -p 127.0.0.1:8443:8080 ghcr.io/coder/code-server:latest
```

Place custom capsules in `~/<root>/capsules/`.

## Ephemeral lifecycle

Templates support fine-grained ephemeral controls:

```yaml
ephemeral:
  transient: true          # libvirt transient domain
  destroy_on_stop: true    # shred disk on stop
  max_runs: 5              # refuse to start after 5 runs
  expires_after_hours: 24  # refuse to start after 24h
```

## Security hardening

Every VM can opt into hardening via the template:

```yaml
security:
  selinux: true            # enforce sVirt SELinux contexts
  no_guest_agent: true     # remove qemu-guest-agent channel
  restrict_network: true   # apply egress nwfilter
  allow_hosts:
    - github.com
    - registry.npmjs.org
```

## Commands

- `latita bootstrap` — host setup
- `latita create <template> --name <name>` — create VM
- `latita start <name>` / `latita stop <name>` / `latita destroy <name>`
- `latita ssh <name>` / `latita connect <name>`
- `latita revive <name>` — recreate domain from saved metadata
- `latita capsule list` / `latita capsule apply <vm> <capsule>`
- `latita template list` / `latita template show <name>`
- `latita doctor` — dependency check

## License

MIT
