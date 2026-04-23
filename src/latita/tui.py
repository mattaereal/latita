from __future__ import annotations

import asyncio
from typing import Optional

from textual.app import App
from textual.binding import Binding
from textual.widgets import Static

from .operations import (
    bootstrap_host,
    connect_instance,
    create_instance,
    destroy_instance,
    run_instance,
    scan_instances,
    ssh_instance,
    start_instance,
    stop_instance,
)


def _build_vm_table_str(entries: list[dict], selected_idx: int = -1) -> str:
    if not entries:
        return '  No VMs found'
    header = f'  {'Name':<20} {'Status':<10} {'IP':<15} {'Profile':<10} {'CPUs':<6} {'Mem':<8}'
    sep = '  ' + '-' * 70
    rows = []
    for i, e in enumerate(entries):
        name = e.get('name', '?')[:20]
        status = e.get('status', '?')[:10]
        ip = e.get('ip') or '—'[:15]
        profile = e.get('profile') or '—'[:10]
        cpus = str(e.get('cpus', '—'))[:6]
        mem = str(e.get('memory', '—'))[:8]
        marker = '> ' if i == selected_idx else '  '
        rows.append(f'{marker}{name:<20} {status:<10} {ip:<15} {profile:<10} {cpus:<6} {mem:<8}')
    return '\n'.join([header, sep] + rows)


class Dashboard(App):

    CSS = '''
    Screen {
        background: #1e1e2e;
    }

    #sidebar {
        width: 28;
        padding: 1 2;
        background: #16161e;
        border-right: solid #89b4fa;
    }

    #sidebar-title {
        text-align: center;
        color: #89b4fa;
        text-style: bold;
        margin-bottom: 1;
    }

    #sidebar-info {
        color: #7f849c;
        margin-bottom: 2;
    }

    #actions {
        color: #cdd6f4;
        height: auto;
    }

    #main-area {
        width: 1fr;
        height: auto;
        padding: 1 3;
        overflow: hidden auto;
    }

    #vm-table-container {
        overflow: hidden auto;
    }

    #statusbar {
        height: 1;
        padding: 0 2;
        background: #16161e;
        border-top: solid #313244;
        color: #7f849c;
    }
    '''

    BINDINGS = [
        Binding('q', 'quit', 'Quit', show=False),
        Binding('1', 'action_1', '', show=False),
        Binding('2', 'action_2', '', show=False),
        Binding('3', 'action_3', '', show=False),
        Binding('4', 'action_4', '', show=False),
        Binding('5', 'action_5', '', show=False),
        Binding('6', 'action_6', '', show=False),
        Binding('7', 'action_7', '', show=False),
        Binding('8', 'action_8', '', show=False),
        Binding('9', 'action_9', '', show=False),
        Binding('up', 'cursor_up', '', show=False),
        Binding('down', 'cursor_down', '', show=False),
        Binding('enter', 'confirm_action', '', show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._refresh_task: Optional[asyncio.Task] = None
        self._suspend_refresh = False
        self._selected_idx = 0
        self._vm_list: list[dict] = []

    def compose(self):
        yield Static('Latita', id='sidebar-title')
        yield Static('VMs: —', id='sidebar-info')
        yield Static(self._sidebar_str(), id='actions', markup=False)
        yield Static('', id='main-area')

    def _sidebar_str(self) -> str:
        actions = [
            ('1', 'Create VM'),
            ('2', 'Run one-shot'),
            ('3', 'List VMs'),
            ('4', 'Start'),
            ('5', 'Stop'),
            ('6', 'Destroy'),
            ('7', 'SSH'),
            ('8', 'Connect'),
            ('9', 'Bootstrap'),
        ]
        return '\n'.join(f'  [{a}] {n}' for a, n in actions)

    def on_mount(self) -> None:
        self._refresh_vm_list()
        self._refresh_task = self.set_interval(2.0, self._refresh_vm_list)
        self._update_vm_display()

    def _refresh_vm_list(self) -> None:
        if not self._suspend_refresh:
            new_list = scan_instances()
            if new_list != self._vm_list:
                self._vm_list = new_list
                self._update_vm_display()

    def _update_vm_display(self) -> None:
        main_area = self.query_one('#main-area', Static)
        vm_str = _build_vm_table_str(self._vm_list, self._selected_idx)
        total = len(self._vm_list)
        info = self.query_one('#sidebar-info', Static)
        info.update(f'VMs: {total}  |  navigate with up/down keys')
        main_area.update(f'VMs ({total})\n{vm_str}')

    def _with_refresh(self, fn: callable) -> None:
        self._suspend_refresh = True
        try:
            fn()
        finally:
            self._suspend_refresh = False
            self._refresh_vm_list()
            self._update_vm_display()

    def action_1(self) -> None:
        self._with_refresh(self._action_create)

    def action_2(self) -> None:
        self._with_refresh(self._action_run)

    def action_3(self) -> None:
        self._with_refresh(self._action_list)

    def action_4(self) -> None:
        self._with_refresh(self._action_start)

    def action_5(self) -> None:
        self._with_refresh(self._action_stop)

    def action_6(self) -> None:
        self._with_refresh(self._action_destroy)

    def action_7(self) -> None:
        self._with_refresh(self._action_ssh)

    def action_8(self) -> None:
        self._with_refresh(self._action_connect)

    def action_9(self) -> None:
        self._with_refresh(self._action_bootstrap)

    def action_cursor_up(self) -> None:
        if self._vm_list:
            self._selected_idx = (self._selected_idx - 1) % len(self._vm_list)
            self._highlight_selected()

    def action_cursor_down(self) -> None:
        if self._vm_list:
            self._selected_idx = (self._selected_idx + 1) % len(self._vm_list)
            self._highlight_selected()

    def _highlight_selected(self) -> None:
        self._update_vm_display()

    def action_confirm_action(self) -> None:
        if self._vm_list and 0 <= self._selected_idx < len(self._vm_list):
            name = self._vm_list[self._selected_idx]['name']
            self._action_ssh_to(name)

    def _selected_name(self) -> str | None:
        if self._vm_list and 0 <= self._selected_idx < len(self._vm_list):
            return self._vm_list[self._selected_idx]['name']
        return None

    def _action_create(self) -> None:
        from .prompts import interactive_create_simple
        try:
            recipe = interactive_create_simple()
        except Exception:
            return
        template_name = recipe.get('template_name', recipe.get('profile', 'headless'))
        try:
            create_instance(template_name, name=recipe.get('name'), overrides=recipe)
            self.notify('VM created successfully')
        except Exception as exc:
            self.notify(f'Create failed: {exc}', severity='error')

    def _action_run(self) -> None:
        from .prompts import interactive_create_simple
        try:
            recipe = interactive_create_simple()
        except Exception:
            return
        template_name = recipe.get('template_name', recipe.get('profile', 'headless'))
        try:
            run_instance(template_name, overrides=recipe)
        except Exception as exc:
            self.notify(f'Run failed: {exc}', severity='error')

    def _action_list(self) -> None:
        vm_str = _build_vm_table_str(self._vm_list, self._selected_idx)
        self.notify(f'VMs ({len(self._vm_list)})\n{vm_str[:300]}', timeout=6.0)

    def _action_start(self) -> None:
        name = self._selected_name()
        if not name:
            self.notify('Select a VM with ↑↓ first', severity='warning')
            return
        try:
            start_instance(name)
            self.notify(f'{name} started')
        except Exception as exc:
            self.notify(f'Start failed: {exc}', severity='error')

    def _action_stop(self) -> None:
        name = self._selected_name()
        if not name:
            self.notify('Select a VM with ↑↓ first', severity='warning')
            return
        try:
            stop_instance(name)
            self.notify(f'{name} stopped')
        except Exception as exc:
            self.notify(f'Stop failed: {exc}', severity='error')

    def _action_destroy(self) -> None:
        name = self._selected_name()
        if not name:
            self.notify('Select a VM with ↑↓ first', severity='warning')
            return
        try:
            destroy_instance(name)
            self.notify(f'{name} destroyed')
        except Exception as exc:
            self.notify(f'Destroy failed: {exc}', severity='error')

    def _action_ssh_to(self, name: str) -> None:
        try:
            ssh_instance(name)
        except Exception as exc:
            self.notify(f'SSH failed: {exc}', severity='error')

    def _action_ssh(self) -> None:
        name = self._selected_name()
        if not name:
            self.notify('Select a VM with ↑↓ first', severity='warning')
            return
        self._action_ssh_to(name)

    def _action_connect(self) -> None:
        name = self._selected_name()
        if not name:
            self.notify('Select a VM with ↑↓ first', severity='warning')
            return
        try:
            connect_instance(name)
        except Exception as exc:
            self.notify(f'Connect failed: {exc}', severity='error')

    def _action_bootstrap(self) -> None:
        try:
            bootstrap_host()
            self.notify('Bootstrap complete')
        except Exception as exc:
            self.notify(f'Bootstrap failed: {exc}', severity='error')