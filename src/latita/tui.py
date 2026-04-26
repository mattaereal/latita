from __future__ import annotations

import os
import shutil
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any, Callable, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    ListView,
    ListItem,
    Select,
    Static,
)
from rich.console import Console
from rich.pretty import Pretty

from .config import (
    get_capsule_path,
    get_config,
    get_template_path,
    is_builtin_capsule,
    is_builtin_template,
    list_capsules,
    list_latita_templates,
    write_yaml,
)
from .operations import (
    _detect_video_models,
    apply_capsule_live,
    bootstrap_host,
    connect_instance,
    create_instance,
    destroy_instance,
    doctor,
    run_instance,
    scan_instances,
    ssh_instance,
    start_instance,
    stop_instance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_editor(path: Any) -> None:
    """Open a file in $EDITOR."""
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(path)])


def _ensure_running(name: str) -> None:
    """Start a VM if it is not already running."""
    entries = {e["name"]: e for e in scan_instances()}
    if entries.get(name, {}).get("status") != "running":
        start_instance(name)


# ---------------------------------------------------------------------------
# Action list item
# ---------------------------------------------------------------------------

class ActionItem(ListItem):
    def __init__(self, action_id: str | None, label: str, **kwargs: Any) -> None:
        super().__init__(Label(label), **kwargs)
        self.action_id = action_id


# ---------------------------------------------------------------------------
# Confirm modal
# ---------------------------------------------------------------------------

class ConfirmScreen(Screen):
    """Simple Yes/No modal. 'No' is the default focus to prevent accidental confirms."""

    BINDINGS = [
        Binding("y", "yes", "Yes", show=False),
        Binding("n", "no", "No", show=False),
        Binding("escape", "no", "No", show=False),
    ]

    def __init__(self, message: str, on_result: Callable[[bool], None]) -> None:
        super().__init__()
        self.message = message
        self.on_result = on_result

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self.message, id="confirm-msg")
            with Horizontal(id="confirm-buttons", classes="form-buttons"):
                yield Button("No", id="btn-no", variant="primary")
                yield Button("Yes", id="btn-yes", variant="error")

    def on_mount(self) -> None:
        self.query_one("#btn-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-yes":
            self.action_yes()
        elif event.button.id == "btn-no":
            self.action_no()

    def action_yes(self) -> None:
        self.app.pop_screen()
        self.on_result(True)

    def action_no(self) -> None:
        self.app.pop_screen()
        self.on_result(False)


class TypeToConfirmScreen(Screen):
    """Type a confirmation word + Enter to proceed. Prevents accidental Enter spam."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, message: str, confirm_word: str, on_result: Callable[[bool], None]) -> None:
        super().__init__()
        self.message = message
        self.confirm_word = confirm_word
        self.on_result = on_result

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self.message, id="confirm-msg")
            yield Static(f"Type '{self.confirm_word}' and press Enter to confirm. Esc to cancel.", id="confirm-hint")
            yield Input(placeholder=f"type: {self.confirm_word}", id="confirm-input")
            yield Static("", id="confirm-error")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "confirm-input":
            self._check()

    def _check(self) -> None:
        inp = self.query_one("#confirm-input", Input)
        err = self.query_one("#confirm-error", Static)
        if inp.value.strip().lower() == self.confirm_word.lower():
            self.app.pop_screen()
            self.on_result(True)
        else:
            err.update(f"Wrong. Expected '{self.confirm_word}'.")
            inp.value = ""
            inp.focus()

    def action_cancel(self) -> None:
        self.app.pop_screen()
        self.on_result(False)


# ---------------------------------------------------------------------------
# Form screen base (Create VM / Run VM)
# ---------------------------------------------------------------------------

class FormScreen(Screen[dict[str, Any] | None]):
    """Base modal form with profile, name, network, video model, error, buttons."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", show=False),
    ]

    _name_counters: dict[str, int] = {}

    def __init__(self, title: str, box_id: str) -> None:
        super().__init__()
        self._title = title
        self._box_id = box_id
        self._video_options = self._load_video_options()

    @staticmethod
    def _load_video_options() -> tuple[list[tuple[str, str]], str]:
        """Probe QEMU and return (Select options, default value)."""
        try:
            models = _detect_video_models()
        except Exception:
            return ([("Auto-detect", "")], "")
        available = models["available"]
        best = models["best"]
        labels = {
            "qxl": "qxl   (best SPICE)",
            "virtio": "virtio (good perf)",
            "vga": "vga    (universal)",
        }
        opts = []
        default = ""
        for model in ("qxl", "virtio", "vga"):
            if available.get(model):
                opts.append((labels[model], model))
                if model == best:
                    default = model
        if not opts:
            opts = [("Auto-detect", "")]
        return (opts, default)

    def _suggest_name(self, profile: str) -> str:
        """Return the next sequential name for a profile."""
        self._name_counters[profile] = self._name_counters.get(profile, 0) + 1
        return f"{profile}-{self._name_counters[profile]}"

    def compose(self) -> ComposeResult:
        with Vertical(id=self._box_id, classes="form-box"):
            yield Static(self._title, id="form-title", classes="form-title")
            yield from self._compose_fields()
            yield Static("", id="form-error", classes="form-error")
            with Horizontal(id="form-buttons", classes="form-buttons"):
                yield Button("Create", id="btn-create", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def _compose_fields(self) -> ComposeResult:
        """Child classes override to yield extra widgets."""
        yield Select(
            [("headless", "headless"), ("desktop", "desktop")],
            value="headless",
            id="profile",
        )
        yield Input(placeholder="VM name", id="name")
        yield Select(
            [("NAT (shared with host)", "nat"), ("Isolated (no internet)", "isolated"), ("None (no network device)", "none")],
            value="nat",
            id="network_mode",
        )
        video_opts, video_default = self._video_options
        yield Select(video_opts, value=video_default or None, id="video_model")
        yield Static("Video model (desktop VMs only)", id="video-hint", classes="form-hint")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "profile":
            profile = str(event.value) if event.value else "headless"
            self._toggle_video_visibility(profile)
            # Auto-fill suggested name when profile changes
            name_widget = self.query_one("#name", Input)
            if not name_widget.value.strip():
                name_widget.value = self._suggest_name(profile)
                name_widget.focus()
        elif event.select.id == "network_mode":
            self._focus_next_after("#network_mode")
        elif event.select.id == "video_model":
            self._focus_next_after("#video_model")

    def _focus_next_after(self, widget_id: str) -> None:
        """Focus the next focusable widget after the given one."""
        order = ["#profile", "#name", "#network_mode", "#video_model", "#transient", "#destroy_on_stop", "#command", "#btn-create", "#btn-cancel"]
        try:
            idx = order.index(widget_id)
        except ValueError:
            return
        for next_id in order[idx + 1:]:
            try:
                widget = self.query_one(next_id)
                if hasattr(widget, "focus") and getattr(widget, "display", True) != "none":
                    widget.focus()
                    break
            except Exception:
                continue

    def _toggle_video_visibility(self, profile: str) -> None:
        video = self.query_one("#video_model", Select)
        hint = self.query_one("#video-hint", Static)
        if profile == "desktop":
            video.styles.display = "block"
            hint.styles.display = "block"
        else:
            video.styles.display = "none"
            hint.styles.display = "none"

    def on_mount(self) -> None:
        profile_widget = self.query_one("#profile", Select)
        self._toggle_video_visibility(str(profile_widget.value) if profile_widget.value else "headless")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-create":
            self.action_submit()
        elif event.button.id == "btn-cancel":
            self.action_dismiss()

    def action_submit(self) -> None:
        name_widget = self.query_one("#name", Input)
        profile_widget = self.query_one("#profile", Select)
        net_widget = self.query_one("#network_mode", Select)
        error_widget = self.query_one("#form-error", Static)

        name = name_widget.value.strip()
        profile = profile_widget.value
        net_mode = str(net_widget.value) if net_widget.value else "nat"

        if not name:
            error_widget.update("Name is required")
            name_widget.focus()
            return
        if profile is None:
            profile = "headless"
        result = self._build_result(name, profile, net_mode)
        self.dismiss(result)

    def _build_result(self, name: str, profile: str, net_mode: str) -> dict[str, Any]:
        """Child classes override to add extra fields."""
        recipe: dict[str, Any] = {
            "profile": profile,
            "template_name": profile,
            "name": name,
            "network": {
                "mode": net_mode,
                "nat_network": "default" if net_mode == "nat" else "",
            },
        }
        if profile == "desktop":
            video_widget = self.query_one("#video_model", Select)
            video = str(video_widget.value) if video_widget.value else ""
            if video:
                recipe["video_model"] = video
        return recipe

    def action_dismiss(self) -> None:
        self.dismiss(None)


class CreateVMScreen(FormScreen):
    """Native TUI form for creating a persistent VM."""

    def __init__(self) -> None:
        super().__init__("Create VM", "create-box")

    def _compose_fields(self) -> ComposeResult:
        yield from super()._compose_fields()
        yield Checkbox("Transient (auto-remove on shutdown)", value=False, id="transient")
        yield Checkbox("Destroy on stop", value=False, id="destroy_on_stop")

    def _build_result(self, name: str, profile: str, net_mode: str) -> dict[str, Any]:
        recipe = super()._build_result(name, profile, net_mode)
        transient = self.query_one("#transient", Checkbox).value
        destroy = self.query_one("#destroy_on_stop", Checkbox).value
        if transient:
            recipe.setdefault("ephemeral", {})["transient"] = True
        if destroy:
            recipe.setdefault("ephemeral", {})["destroy_on_stop"] = True
        return {"mode": "create", "recipe": recipe}


class RunVMScreen(FormScreen):
    """Native TUI form for running a one-shot ephemeral VM."""

    def __init__(self) -> None:
        super().__init__("Run one-shot VM", "run-box")

    def _compose_fields(self) -> ComposeResult:
        yield from super()._compose_fields()
        yield Input(placeholder="Command to run inside VM (optional, e.g. uname -a)", id="command")
        yield Static("This VM is transient and will be destroyed on shutdown.", id="run-warn", classes="form-warn")

    def _build_result(self, name: str, profile: str, net_mode: str) -> dict[str, Any]:
        recipe = super()._build_result(name, profile, net_mode)
        command = self.query_one("#command", Input).value.strip()
        if command:
            recipe["command"] = command
        return {"mode": "run", "recipe": recipe}


# ---------------------------------------------------------------------------
# Apply capsule screen
# ---------------------------------------------------------------------------

class ApplyCapsuleScreen(Screen[str | None]):
    """Native TUI picker for applying a capsule."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", show=False),
    ]

    def __init__(self, vm_name: str) -> None:
        super().__init__()
        self.vm_name = vm_name
        self._net_warn = self._check_network(vm_name)

    def _check_network(self, name: str) -> str | None:
        from .metadata import read_instance_recipe, read_instance_spec
        recipe = read_instance_recipe(name)
        spec = read_instance_spec(name)
        net_mode = ""
        if recipe:
            net_mode = recipe.get("network", {}).get("mode", "")
        elif spec:
            net_mode = spec.get("net_mode", "")
        if net_mode in ("isolated", "none", ""):
            return f"Warning: VM has no internet ({net_mode or 'unknown'}). Capsules that download will fail."
        return None

    def compose(self) -> ComposeResult:
        with Vertical(id="cap-box", classes="form-box"):
            yield Static(f"Apply capsule to {self.vm_name}", id="cap-title", classes="form-title")
            if self._net_warn:
                yield Static(
                    "\n".join([
                        "NETWORK WARNING",
                        self._net_warn,
                        "Capsules that download will fail!",
                    ]),
                    id="cap-net-warn",
                )
            caps = list(list_capsules().keys())
            if caps:
                yield Select([(c, c) for c in caps], value=caps[0], id="capsule")
            else:
                yield Static("No capsules available", id="cap-none")
            yield Static("Tab to navigate, Space/Enter to activate", id="cap-hint")
            with Horizontal(id="cap-buttons", classes="form-buttons"):
                yield Button("Apply", id="btn-apply", variant="primary")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-apply":
            self.action_submit()
        elif event.button.id == "btn-cancel":
            self.action_dismiss()

    def action_submit(self) -> None:
        caps = list(self.query("#capsule"))
        if not caps:
            self.dismiss(None)
            return
        cap_widget = caps[0]
        assert isinstance(cap_widget, Select)
        value = cap_widget.value
        if value is None:
            self.notify("Select a capsule first", severity="warning")
            return
        self.dismiss(str(value))

    def action_dismiss(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Info screen
# ---------------------------------------------------------------------------

class InfoScreen(Screen):
    """Show detailed metadata for a VM."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back", show=False),
        Binding("q", "app.pop_screen", "Back", show=False),
    ]

    def __init__(self, vm_entry: dict[str, Any]) -> None:
        super().__init__()
        self.vm_entry = vm_entry

    def compose(self) -> ComposeResult:
        name = self.vm_entry.get("name", "unknown")
        with Vertical(id="info-box"):
            yield Static(f"VM Info: {name}", id="info-title")
            with ScrollableContainer(id="info-scroll"):
                yield Static("", id="info-detail")
            yield Static("[Esc/q] Back", id="info-hint")

    def on_mount(self) -> None:
        detail = self.query_one("#info-detail", Static)
        detail.update(Pretty(self._build_detail()))

    def _build_detail(self) -> dict[str, Any]:
        from .metadata import read_instance_spec, read_instance_recipe
        e = self.vm_entry
        name = e.get("name", "unknown")
        spec = read_instance_spec(name)
        recipe = read_instance_recipe(name)
        detail: dict[str, Any] = {
            "name": name,
            "status": e.get("status", "?"),
            "ip": e.get("ip") or e.get("mgmt_ip") or "—",
            "profile": e.get("profile", "?"),
            "template": e.get("template", "?"),
            "cpus": e.get("cpus", "?"),
            "memory": e.get("memory", "?"),
            "applied_capsules": e.get("applied_capsules", []),
        }
        if spec:
            detail.update({
                "transient": spec.get("transient", False),
                "destroy_on_stop": spec.get("destroy_on_stop", False),
                "max_runs": spec.get("max_runs"),
                "run_count": spec.get("run_count", 0),
                "expire_at": spec.get("expire_at"),
                "created_at": spec.get("created_at"),
                "base_image": spec.get("base_image", "?"),
                "net_mode": spec.get("net_mode", "?"),
                "graphics": spec.get("graphics", "none"),
            })
        if recipe:
            detail["os_family"] = recipe.get("os_family", "?")
            detail["guest_user"] = recipe.get("guest_user", "?")
        return detail


# ---------------------------------------------------------------------------
# Browser screen base (Templates / Capsules)
# ---------------------------------------------------------------------------

class BrowserScreen(Screen):
    """Base two-pane browser with list, detail, and CRUD actions."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back", show=True),
        Binding("q", "app.pop_screen", "Back", show=False),
        Binding("tab", "toggle_pane", "Toggle pane", show=True),
        Binding("e", "edit", "Edit", show=True),
        Binding("enter", "edit", "Edit", show=False),
        Binding("d", "delete", "Delete", show=True),
        Binding("r", "rename", "Rename", show=True),
        Binding("y", "duplicate", "Duplicate", show=True),
        Binding("n", "new", "New", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._items: dict[str, Any] = {}

    # --- Abstract hooks ---

    def _browser_title(self) -> str:
        raise NotImplementedError

    def _table_columns(self) -> list[str]:
        raise NotImplementedError

    def _load_items(self) -> dict[str, Any]:
        raise NotImplementedError

    def _detail_for(self, name: str) -> Any:
        raise NotImplementedError

    def _is_builtin(self, name: str) -> bool:
        raise NotImplementedError

    def _get_path(self, name: str) -> Path:
        raise NotImplementedError

    def _file_ext(self) -> str:
        raise NotImplementedError

    def _new_schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def _copy_builtin(self, name: str, dst: Path) -> None:
        raise NotImplementedError

    # --- Compose & lifecycle ---

    def compose(self) -> ComposeResult:
        yield Static(self._browser_title(), id="browser-title")
        with Horizontal(id="browser-body"):
            yield DataTable(id="browser-left", cursor_type="row")
            with Vertical(id="browser-right"):
                with ScrollableContainer(id="browser-detail-scroll"):
                    yield Static("Select an item.\n", id="browser-detail")
                yield Static(
                    "Shortcuts\n"
                    "  Tab    Toggle list / detail\n"
                    "  Enter  Edit in $EDITOR\n"
                    "  d      Delete\n"
                    "  r      Rename\n"
                    "  y      Duplicate\n"
                    "  n      New\n"
                    "  Esc    Back",
                    id="browser-actions",
                )

    def on_mount(self) -> None:
        self._refresh_items()
        self.query_one("#browser-left", DataTable).focus()

    # --- Refresh & selection ---

    def _refresh_items(self) -> None:
        table = self.query_one("#browser-left", DataTable)
        table.clear()
        for col in self._table_columns():
            table.add_column(col)
        self._items = self._load_items()
        for name, data in self._items.items():
            table.add_row(*self._row_cells(name, data))
        if table.row_count:
            table.move_cursor(row=0)
            self._show_detail(0)

    def _row_cells(self, name: str, data: dict[str, Any]) -> list[str]:
        return [name]

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = self.query_one("#browser-left", DataTable)
        cursor = table.cursor_row
        if isinstance(cursor, int):
            self._show_detail(cursor)

    def _selected_name(self) -> str | None:
        table = self.query_one("#browser-left", DataTable)
        cursor = table.cursor_row
        if not isinstance(cursor, int):
            return None
        names = list(self._items.keys())
        if 0 <= cursor < len(names):
            return names[cursor]
        return None

    def _show_detail(self, cursor: int) -> None:
        names = list(self._items.keys())
        if 0 <= cursor < len(names):
            name = names[cursor]
            detail = self.query_one("#browser-detail", Static)
            detail.update(Pretty(self._detail_for(name)))

    def _app(self) -> Dashboard | None:
        app = self.app
        return app if isinstance(app, Dashboard) else None

    # --- Actions ---

    def action_toggle_pane(self) -> None:
        table = self.query_one("#browser-left", DataTable)
        scroll = self.query_one("#browser-detail-scroll", ScrollableContainer)
        if self.focused is table:
            scroll.focus()
        else:
            table.focus()

    def action_edit(self) -> None:
        name = self._selected_name()
        if not name:
            return
        app = self._app()
        if not app:
            return
        path = self._get_path(name)
        if self._is_builtin(name):
            cfg = get_config()
            dst = cfg.templates_dir / f"{name}{self._file_ext()}"
            cfg.templates_dir.mkdir(parents=True, exist_ok=True)
            self._copy_builtin(name, dst)
            path = dst
            app.notify(f"Copied built-in to user directory")
        app._run_command(lambda: _open_editor(path), f"Edit {name}")
        self._refresh_items()

    def action_delete(self) -> None:
        name = self._selected_name()
        if not name:
            return
        if self._is_builtin(name):
            self.notify("Cannot delete built-in items", severity="warning")
            return
        app = self._app()
        if not app:
            return

        def _on_result(confirmed: bool) -> None:
            if confirmed:
                self._get_path(name).unlink()
                self._refresh_items()
                app.notify(f"'{name}' deleted")

        self.app.push_screen(ConfirmScreen(f"Delete '{name}'?", _on_result))

    def action_rename(self) -> None:
        name = self._selected_name()
        if not name:
            return
        app = self._app()
        if not app:
            return
        ext = self._file_ext()

        # If built-in, copy to user directory first (same pattern as edit)
        if self._is_builtin(name):
            cfg = get_config()
            dst = cfg.templates_dir / f"{name}{ext}"
            cfg.templates_dir.mkdir(parents=True, exist_ok=True)
            self._copy_builtin(name, dst)
            app.notify(f"Copied built-in to user directory")
            old_path = dst
        else:
            old_path = self._get_path(name)

        def _do() -> str | None:
            new_name = input("New name (empty = cancel): ").strip()
            if not new_name or new_name == name:
                print("Rename canceled.")
                return None
            new_path = old_path.parent / f"{new_name}{ext}"
            if new_path.exists():
                print(f"'{new_name}' already exists")
                return None
            old_path.rename(new_path)
            return new_name

        result = app._run_command(_do, f"Rename {name}")
        if result:
            self._refresh_items()
            app.notify(f"Renamed to '{result}'")

    def action_duplicate(self) -> None:
        name = self._selected_name()
        if not name:
            return
        app = self._app()
        if not app:
            return
        ext = self._file_ext()
        path = self._get_path(name)
        dst = path.parent / f"{name}-copy{ext}"
        if dst.exists():
            app.notify("A copy already exists", severity="warning")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        self._refresh_items()
        app.notify(f"Duplicated to '{dst.stem}'")

    def action_new(self) -> None:
        app = self._app()
        if not app:
            return
        ext = self._file_ext()
        schema = self._new_schema()

        def _do() -> str | None:
            n = input("Name: ").strip()
            if not n:
                return None
            desc = input("Description: ").strip() or "My custom item"
            path = get_config().templates_dir / f"{n}{ext}"
            if path.exists():
                print(f"'{n}' already exists")
                return None
            schema["description"] = desc
            write_yaml(path, schema)
            _open_editor(path)
            return n

        result = app._run_command(_do, "New item")
        if result:
            self._refresh_items()
            app.notify(f"Created '{result}'")


class TemplatesScreen(BrowserScreen):
    """Full-screen template browser."""

    def _browser_title(self) -> str:
        return "Templates"

    def _table_columns(self) -> list[str]:
        return ["Name", "Profile", "OS", "CPUs", "Memory", "Disk"]

    def _load_items(self) -> dict[str, Any]:
        return list_latita_templates()

    def _row_cells(self, name: str, data: dict[str, Any]) -> list[str]:
        return [
            name,
            str(data.get("profile", "-")),
            str(data.get("os_family", "-")),
            str(data.get("cpus", "-")),
            str(data.get("memory", "-")) if data.get("memory") != "-" else "-",
            str(data.get("disk_size", "-")),
        ]

    def _detail_for(self, name: str) -> Any:
        return self._items.get(name, {})

    def _is_builtin(self, name: str) -> bool:
        return is_builtin_template(name)

    def _get_path(self, name: str) -> Path:
        return get_template_path(name)

    def _file_ext(self) -> str:
        return ".latita"

    def _new_schema(self) -> dict[str, Any]:
        return {
            "profile": "headless",
            "description": "",
            "os_family": "fedora",
            "cpus": 2,
            "memory": 4096,
            "disk_size": "20G",
            "guest_user": "dev",
            "passwordless_sudo": True,
            "network": {
                "mode": "isolated",
                "nat_network": "",
                "mgmt_ip": "10.31.0.10",
                "mgmt_prefix": 24,
            },
            "ephemeral": {
                "transient": True,
                "destroy_on_stop": False,
            },
            "security": {
                "selinux": True,
                "no_guest_agent": True,
                "restrict_network": False,
                "allow_hosts": [],
            },
            "provision": {
                "packages": [],
                "write_files": [],
                "root_commands": [],
                "user_commands": [],
            },
        }

    def _copy_builtin(self, name: str, dst: Path) -> None:
        shutil.copy2(get_template_path(name), dst)


class CapsulesScreen(BrowserScreen):
    """Full-screen capsule browser."""

    def _browser_title(self) -> str:
        return "Capsules"

    def _table_columns(self) -> list[str]:
        return ["Name"]

    def _load_items(self) -> dict[str, Any]:
        return list_capsules()

    def _detail_for(self, name: str) -> Any:
        return self._items.get(name, {})

    def _is_builtin(self, name: str) -> bool:
        return is_builtin_capsule(name)

    def _get_path(self, name: str) -> Path:
        return get_capsule_path(name)

    def _file_ext(self) -> str:
        return ".cap"

    def _new_schema(self) -> dict[str, Any]:
        return {
            "description": "",
            "compatible_profiles": ["headless", "desktop"],
            "compatible_os": ["fedora", "ubuntu", "debian"],
            "live_commands": [],
            "provision": {
                "packages": [],
                "write_files": [],
                "root_commands": [],
                "user_commands": [],
            },
        }

    def _copy_builtin(self, name: str, dst: Path) -> None:
        shutil.copy2(get_capsule_path(name), dst)


# ---------------------------------------------------------------------------
# Dashboard (main screen)
# ---------------------------------------------------------------------------

class Dashboard(App):
    """Minimalistic two-pane TUI dashboard."""

    CSS_PATH = Path(__file__).with_suffix(".tcss")

    BINDINGS = [
        Binding("tab", "toggle_pane", "Switch pane", show=False),
        Binding("q", "quit", "Quit", show=True),
        Binding("c", "create", "Create VM", show=False),
        Binding("r", "run", "Run one-shot", show=False),
        Binding("b", "bootstrap", "Bootstrap", show=False),
        Binding("d", "doctor", "Doctor", show=False),
        Binding("t", "templates", "Templates", show=True),
        Binding("p", "capsules", "Capsules", show=True),
        Binding("s", "start", "Start VM", show=False),
        Binding("S", "stop", "Stop VM", show=False),
        Binding("D", "destroy", "Destroy VM", show=False),
        Binding("h", "ssh", "SSH", show=False),
        Binding("k", "connect", "Connect", show=False),
        Binding("a", "apply_capsule", "Apply Capsule", show=False),
        Binding("i", "info", "Info", show=False),
        Binding("R", "refresh", "Refresh", show=False),
    ]

    selected_vm = reactive(None)

    def __init__(self) -> None:
        super().__init__()
        self._vm_list: list[dict[str, Any]] = []
        self._action_items: dict[str, ActionItem] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Vertical(id="left-pane"):
                yield Static("VMs", id="left-title")
                yield DataTable(id="vm-table", cursor_type="row")
            with Vertical(id="right-pane"):
                yield Static("Actions", id="right-title")
                yield ListView(id="action-list")
        yield Static(
            "c:Create  r:Run  s:Start  S:Stop  D:Destroy  h:SSH  a:Apply  t:Templates  p:Capsules  R:Refresh  q:Quit",
            id="hint-pane",
        )
        yield Static("", id="statusbar")

    def on_mount(self) -> None:
        table = self.query_one("#vm-table", DataTable)
        table.add_columns("Name", "Status", "IP", "Profile", "CPUs", "Mem")
        self._build_action_list()
        self._refresh_vm_list()
        table.focus()

    def _build_action_list(self) -> None:
        action_list = self.query_one("#action-list", ListView)
        specs = [
            ("start", "Start VM", False),
            ("stop", "Stop VM", False),
            ("destroy", "Destroy VM", False),
            ("ssh", "SSH", False),
            ("connect", "Connect", False),
            ("apply_capsule", "Apply Capsule", False),
            ("info", "Info", False),
            (None, "", False),   # spacer
            ("create", "Create VM", True),
            ("run", "Run one-shot", True),
            ("templates", "Templates", True),
            ("capsules", "Capsules", True),
            (None, "", False),   # spacer
            ("bootstrap", "Bootstrap", True),
            ("doctor", "Doctor", True),
            (None, "", False),   # spacer
            ("quit", "Quit", True),
        ]
        for aid, label, _ in specs:
            item = ActionItem(aid, label)
            self._action_items[aid or f"__spacer_{id(item)}"] = item
            action_list.append(item)
        if action_list.children:
            action_list.index = 0

    def watch_selected_vm(self, vm: Optional[dict[str, Any]]) -> None:
        self._update_action_states()
        self._update_statusbar()

    def _update_action_states(self) -> None:
        vm = self.selected_vm
        status = vm.get("status", "") if vm else ""
        states = {
            "create": True,
            "run": True,
            "bootstrap": True,
            "doctor": True,
            "templates": True,
            "capsules": True,
            "start": bool(vm and status != "running"),
            "stop": bool(vm and status == "running"),
            "destroy": bool(vm),
            "ssh": bool(vm),
            "connect": bool(vm),
            "apply_capsule": bool(vm),
            "info": bool(vm),
            "quit": True,
        }
        action_list = self.query_one("#action-list", ListView)
        for key, item in self._action_items.items():
            if key.startswith("__spacer_"):
                item.disabled = True
                continue
            item.disabled = not states.get(key, True)
            item.refresh()

    def _update_statusbar(self) -> None:
        vm = self.selected_vm
        name = vm["name"] if vm else "—"
        total = len(self._vm_list)
        status = self.query_one("#statusbar", Static)
        status.update(f" sel: {name} | {total} VMs")

    # --- Unified runner ------------------------------------------------------

    def _run_command(self, fn: Callable[[], Any], label: str) -> Any:
        """Suspend TUI, run fn in the real terminal, then prompt to return."""
        self._pause_refresh()
        try:
            with self.suspend():
                from latita import ui as _ui
                from latita import operations as _ops
                from latita import capsules as _caps
                from latita import utils as _utils
                from latita import prompts as _prompts

                plain_console = Console(file=sys.__stdout__, color_system="auto", width=120)
                _modules = [_ui, _ops, _caps, _utils, _prompts]
                _old = {mod: getattr(mod, "console", None) for mod in _modules}
                for mod in _modules:
                    if _old[mod] is not None:
                        mod.console = plain_console

                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        try:
                            result = fn()
                        except KeyboardInterrupt:
                            print("\nCanceled.")
                            result = None
                        except Exception as exc:
                            print(f"\nError: {exc}")
                            result = None
                    print(f"\n[latita] {label} — Press Enter to return to menu...")
                    try:
                        input()
                    except (EOFError, KeyboardInterrupt):
                        pass
                finally:
                    for mod in _modules:
                        if _old[mod] is not None:
                            mod.console = _old[mod]
                return result
        finally:
            self._resume_refresh()

    # --- Focus switching -----------------------------------------------------

    def action_toggle_pane(self) -> None:
        vm_table = self.query_one("#vm-table", DataTable)
        action_list = self.query_one("#action-list", ListView)
        if self.focused is vm_table:
            self._ensure_valid_cursor(action_list)
            action_list.focus()
        else:
            vm_table.focus()

    def _ensure_valid_cursor(self, action_list: ListView) -> None:
        """If the highlighted action is disabled, jump to the nearest enabled one."""
        children = list(action_list.children)
        if not children:
            return
        idx = action_list.index if action_list.index is not None else 0
        if 0 <= idx < len(children):
            child = children[idx]
            if isinstance(child, ActionItem) and not child.disabled:
                return
        for direction in (1, -1):
            search_idx = idx
            for _ in range(len(children)):
                search_idx = (search_idx + direction) % len(children)
                child = children[search_idx]
                if isinstance(child, ActionItem) and not child.disabled:
                    action_list.index = search_idx
                    return

    # --- Event handlers ------------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = self.query_one("#vm-table", DataTable)
        cursor = table.cursor_row
        if isinstance(cursor, int) and 0 <= cursor < len(self._vm_list):
            self.selected_vm = self._vm_list[cursor]
        else:
            self.selected_vm = None

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on VM table triggers SSH."""
        self.action_ssh()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter on action list executes the action."""
        item = event.item
        if isinstance(item, ActionItem) and item.action_id:
            self._run_action_id(item.action_id)

    # --- VM list refresh -----------------------------------------------------

    def _refresh_vm_list(self) -> None:
        new_list = scan_instances()
        table = self.query_one("#vm-table", DataTable)
        old_name = self.selected_vm["name"] if self.selected_vm else None  # type: ignore[index]

        table.clear()
        self._vm_list = new_list
        selected_row: Optional[int] = None
        for i, e in enumerate(new_list):
            table.add_row(
                e.get("name", "?"),
                e.get("status", "?"),
                e.get("ip") or e.get("mgmt_ip") or "—",
                e.get("profile") or "—",
                str(e.get("cpus") or "—"),
                str(e.get("memory") or "—"),
            )
            if old_name and e.get("name") == old_name:
                selected_row = i

        if selected_row is not None:
            table.move_cursor(row=selected_row)
            self.selected_vm = new_list[selected_row]
        elif new_list:
            cursor = table.cursor_row
            if isinstance(cursor, int) and 0 <= cursor < len(new_list):
                self.selected_vm = new_list[cursor]
            else:
                table.move_cursor(row=0)
                self.selected_vm = new_list[0]
        else:
            self.selected_vm = None

    # --- Action dispatcher ---------------------------------------------------

    def _run_action_id(self, action_id: str) -> None:
        method = getattr(self, f"action_{action_id}", None)
        if method:
            method()

    def _trigger_refresh(self) -> None:
        """Refresh VM list after a state-changing action completes."""
        self._refresh_vm_list()

    # --- Unified runner ------------------------------------------------------

    def _run_command(self, fn: Callable[[], Any], label: str) -> Any:
        """Suspend TUI, run fn in the real terminal, then prompt to return."""
        with self.suspend():
            from latita import ui as _ui
            from latita import operations as _ops
            from latita import capsules as _caps
            from latita import utils as _utils
            from latita import prompts as _prompts

            plain_console = Console(file=sys.__stdout__, color_system="auto", width=120)
            _modules = [_ui, _ops, _caps, _utils, _prompts]
            _old = {mod: getattr(mod, "console", None) for mod in _modules}
            for mod in _modules:
                if _old[mod] is not None:
                    mod.console = plain_console

            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    try:
                        result = fn()
                    except KeyboardInterrupt:
                        print("\nCanceled.")
                        result = None
                    except Exception as exc:
                        print(f"\nError: {exc}")
                        result = None
                print(f"\n[latita] {label} — Press Enter to return to menu...")
                try:
                    input()
                except (EOFError, KeyboardInterrupt):
                    pass
            finally:
                for mod in _modules:
                    if _old[mod] is not None:
                        mod.console = _old[mod]
            return result

    # --- Screen result callbacks ---------------------------------------------

    def _on_create_done(self, result: dict[str, Any] | None) -> None:
        if result is None:
            return
        recipe = result["recipe"]
        template_name = recipe.get("template_name", recipe.get("profile", "headless"))
        self._run_command(
            lambda: create_instance(template_name, name=recipe.get("name"), overrides=recipe),
            "Create VM",
        )
        self._trigger_refresh()

    def _on_run_done(self, result: dict[str, Any] | None) -> None:
        if result is None:
            return
        recipe = result["recipe"]
        template_name = recipe.get("template_name", recipe.get("profile", "headless"))
        command = recipe.get("command")
        self._run_command(
            lambda: run_instance(
                template_name,
                command=command.split() if command else None,
                overrides=recipe,
            ),
            "Run one-shot VM",
        )
        self._trigger_refresh()

    def _on_capsule_chosen(self, capsule_name: str | None) -> None:
        if capsule_name is None:
            return
        name = self._selected_name()
        if not name:
            return
        self._run_command(
            lambda: (_ensure_running(name), apply_capsule_live(name, capsule_name))[1],
            f"Apply capsule to {name}",
        )

    # --- Global actions ------------------------------------------------------

    def action_quit(self) -> None:
        self.exit()

    def action_refresh(self) -> None:
        self._trigger_refresh()

    def action_create(self) -> None:
        self.push_screen(CreateVMScreen(), self._on_create_done)

    def action_run(self) -> None:
        self.push_screen(RunVMScreen(), self._on_run_done)

    def action_bootstrap(self) -> None:
        def _do() -> None:
            try:
                bootstrap_host()
            except Exception as exc:
                print(f"Bootstrap failed: {exc}")
        self._run_command(_do, "Bootstrap")

    def action_doctor(self) -> None:
        def _do() -> None:
            try:
                doctor()
            except Exception as exc:
                print(f"Doctor failed: {exc}")
        self._run_command(_do, "Doctor")

    def action_templates(self) -> None:
        self.push_screen(TemplatesScreen())

    def action_capsules(self) -> None:
        self.push_screen(CapsulesScreen())

    # --- VM actions ----------------------------------------------------------

    def action_start(self) -> None:
        name = self._selected_name()
        if not name:
            self.notify("Select a VM first", severity="warning")
            return
        self._run_command(lambda: start_instance(name), f"Start {name}")
        self._trigger_refresh()

    def action_stop(self) -> None:
        name = self._selected_name()
        if not name:
            self.notify("Select a VM first", severity="warning")
            return
        self._run_command(lambda: stop_instance(name), f"Stop {name}")
        self._trigger_refresh()

    def action_destroy(self) -> None:
        name = self._selected_name()
        if not name:
            self.notify("Select a VM first", severity="warning")
            return

        def _on_result(confirmed: bool) -> None:
            if confirmed:
                self._run_command(lambda: destroy_instance(name), f"Destroy {name}")
                self._trigger_refresh()

        self.push_screen(TypeToConfirmScreen(f"Destroy VM '{name}' and shred its disk?", "destroy", _on_result))

    def action_ssh(self) -> None:
        name = self._selected_name()
        if not name:
            self.notify("Select a VM first", severity="warning")
            return
        _ensure_running(name)
        self._run_command(lambda: ssh_instance(name), f"SSH {name}")

    def action_connect(self) -> None:
        name = self._selected_name()
        if not name:
            self.notify("Select a VM first", severity="warning")
            return
        _ensure_running(name)

        from .metadata import read_instance_spec
        spec = read_instance_spec(name)
        if spec and spec.get("graphics") == "spice":
            # GUI viewer — launch detached so the TUI stays alive
            subprocess.Popen(
                ["virt-viewer", "--connect", get_config().libvirt_uri, "--wait", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.notify(f"Launched virt-viewer for {name}")
        else:
            self._run_command(lambda: ssh_instance(name), f"Connect {name}")

    def action_apply_capsule(self) -> None:
        name = self._selected_name()
        if not name:
            self.notify("Select a VM first", severity="warning")
            return
        self.push_screen(ApplyCapsuleScreen(name), self._on_capsule_chosen)

    def action_info(self) -> None:
        vm = self.selected_vm
        if not vm:
            self.notify("Select a VM first", severity="warning")
            return
        self.push_screen(InfoScreen(vm))

    def _selected_name(self) -> str | None:
        vm = self.selected_vm
        return vm["name"] if vm else None
