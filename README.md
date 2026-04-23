# Latita - Ephemeral libvirt/QEMU lab manager with capsules

Spin up isolated VMs in seconds, harden them automatically, and extend them with drop-in capsules.

Inspired by the simplicity of container runners like [smolvm](https://github.com/smol-machines/smolvm) — but built on standard QEMU/libvirt so it runs anywhere Linux does.

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

# Interactive hierarchical menu (great for daily use)
latita menu

# Run a one-shot command in an ephemeral VM (auto-cleaned on exit)
latita run headless -- uname -a

# Create a persistent headless VM
latita create headless --name myvm

# Create with overrides (no interactive prompts)
latita create headless --name bigvm --cpus 4 --memory 8192 --disk 40G

# Enable networking and apply a capsule
latita create headless --name webdev --net --capsule code-server

# Create a desktop VM
latita create desktop --name mydesktop

# List VMs
latita list

# SSH into a VM
latita ssh myvm

# Apply a capsule to a running VM
latita capsule apply myvm code-server

# Stop (destroy_on_stop VMs are shredded automatically)
latita stop myvm
```

## Project-level configuration

Drop a `.latita` file in any directory. `latita create` automatically picks it up and merges with CLI flags:

```yaml
# .latita
template: headless
memory: 8192
cpus: 4
network:
  mode: nat
capsules:
  - code-server
```

```bash
# Uses .latita defaults, overridden by CLI flags
latita create --name myvm --cpus 2
```

## Interactive creation modes

- `latita create` — **simple mode** (default): 2 prompts (profile + name). Template provides everything else.
- `latita create --advanced` — add resource overrides and capsules
- `latita create --full` — full wizard, every knob exposed, grouped by category

## One-shot ephemeral runner (`latita run`)

No persistent state, perfect for CI and one-off scripts:

```bash
# Ephemeral VM, auto-cleaned on shutdown
latita run headless -- echo "hello from vm"

# With networking and resource overrides
latita run headless --net --cpus 2 --memory 4096 -- python3 --version
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
  mode: isolated        # no network by default
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

Generate a new template interactively:

```bash
latita template generate -o myteam.latita
```

## Capsules

Capsules are YAML files with the `.cap` extension. They extend VMs at **creation time** (cloud-init `provision` block runs once at first boot) or **live** (`live` commands run via SSH on a running VM).

### Writing a capsule

```yaml
# capsules/myapp.cap
description: My custom application
compatibility:
  profiles: [headless, desktop]   # optional filter
  os_family: [fedora, ubuntu]     # optional filter
depends_on: [podman-host]         # optional: other capsules to auto-include

provision:
  packages: [curl, jq]
  write_files:
    - path: /etc/myapp/config.yaml
      permissions: "0644"
      content: |
        key: value
  root_commands:
    - systemctl enable --now myapp
  user_commands:
    - echo "hello {guest_user}"

live:
  user: dev                        # SSH user for live commands
  commands:
    - systemctl restart myapp
```

**Placeholders** like `{guest_user}` are substituted at runtime with the template's guest user.

### Dependency system

Capsules can declare `depends_on: [other-capsule]`. When you request a capsule, its dependencies are automatically resolved, deduplicated, and ordered so dependencies provision before dependents. Cycles are rejected with a clear error.

```yaml
# code-server automatically pulls podman-host
depends_on: [podman-host]
```

### Built-in capsules

| Capsule | What it does | Dependencies |
|---------|-------------|--------------|
| `podman-host` | Rootless Podman + container-selinux | — |
| `docker-host` | Docker CE (rootless-capable) | — |
| `code-server` | VS Code in browser via Podman | `podman-host` |
| `tailscale` | Tailscale mesh VPN | — |
| `ollama` | Local LLM server (Llama, Mistral, etc.) | — |
| `open-webui` | ChatGPT-like UI for Ollama | `ollama` |
| `whisper` | Whisper.cpp local transcription | `ollama` |
| `hermes` | Hermes agent framework | — |
| `openclaw` | OpenClaw AI agent | — |
| `ai-agents` | **All major AI coding agents** in one shot | `hermes`, `openclaw` |

The `ai-agents` capsule installs: Claude Code, OpenAI Codex, Google Gemini CLI, Kimi CLI, OpenCode, OpenClaw, Hermes, and Aider.

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

Every VM is hardened by default. Security features are **opt-out** rather than opt-in:

```yaml
security:
  selinux: true            # enforce sVirt SELinux contexts
  no_guest_agent: true     # remove qemu-guest-agent channel
  restrict_network: true   # apply egress nwfilter
  allow_hosts:
    - github.com
    - registry.npmjs.org
```

Networking is **off by default** (`mode: isolated`). Enable with `--net` or `--network nat`.

## Commands

- `latita bootstrap` — host setup
- `latita create <template> [options]` — create VM (simple by default)
  - `--name`, `--cpus`, `--memory`, `--disk`
  - `--net` / `--network` — enable NAT networking
  - `--allow-host` — allow specific egress hosts
  - `--capsule` — apply capsule at creation time
  - `--ephemeral` / `--transient` — lifecycle flags
  - `--advanced` — interactive advanced mode
  - `--full` — interactive full wizard
- `latita run <template> [options] -- <command>` — one-shot ephemeral VM
- `latita start <name>` / `latita stop <name>` / `latita destroy <name>`
- `latita ssh <name>` / `latita connect <name>`
- `latita revive <name>` — recreate domain from saved metadata
- `latita capsule list` / `latita capsule apply <vm> <capsule>`
- `latita template list` / `latita template show <name>` / `latita template generate`
- `latita doctor` — dependency check

## License

MIT
