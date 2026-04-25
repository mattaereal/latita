from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.containers import ScrollableContainer
from textual.widgets import Button, DataTable, ListView, Select, Input, Checkbox, Static

from latita.tui import (
    Dashboard,
    TemplatesScreen,
    CapsulesScreen,
    ConfirmScreen,
    CreateVMScreen,
    RunVMScreen,
    ApplyCapsuleScreen,
)


def _run_async(coro):
    return asyncio.run(coro)


class TestDashboardStructure:
    def test_compose_has_table_and_actions(self):
        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app.query_one("#vm-table", DataTable) is not None
                assert app.query_one("#action-list", ListView) is not None
                assert app.query_one("#hint-pane") is not None
                assert app.query_one("#statusbar") is not None
                await pilot.press("q")
        _run_async(_test())

    def test_bindings_exist(self):
        app = Dashboard()
        keys = {b.key for b in app.BINDINGS}
        assert "q" in keys
        assert "c" in keys
        assert "r" in keys
        assert "s" in keys
        assert "S" in keys
        assert "D" in keys
        assert "h" in keys
        assert "k" in keys
        assert "a" in keys
        assert "t" in keys
        assert "p" in keys


class TestDashboardVmList:
    def test_vm_list_populated(self, monkeypatch):
        fake_entries = [
            {"name": "vm1", "status": "running", "ip": "10.0.0.1", "profile": "headless", "cpus": 2, "memory": 4096},
            {"name": "vm2", "status": "shut off", "ip": None, "profile": "desktop", "cpus": 4, "memory": 8192},
        ]
        monkeypatch.setattr("latita.tui.scan_instances", lambda: fake_entries)

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                table = app.query_one("#vm-table", DataTable)
                assert table.row_count == 2
                await pilot.press("q")
        _run_async(_test())

    def test_vm_selection_updates_actions(self, monkeypatch):
        fake_entries = [
            {"name": "vm1", "status": "running", "ip": "10.0.0.1", "profile": "headless", "cpus": 2, "memory": 4096},
        ]
        monkeypatch.setattr("latita.tui.scan_instances", lambda: fake_entries)

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                table = app.query_one("#vm-table", DataTable)
                assert table.row_count == 1
                assert app.selected_vm is not None
                assert app.selected_vm["name"] == "vm1"
                action_list = app.query_one("#action-list", ListView)
                assert len(action_list.children) > 0
                await pilot.press("q")
        _run_async(_test())

    def test_empty_vm_list_shows_hint(self, monkeypatch):
        monkeypatch.setattr("latita.tui.scan_instances", lambda: [])

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                table = app.query_one("#vm-table", DataTable)
                assert table.row_count == 0
                assert app.selected_vm is None
                await pilot.press("q")
        _run_async(_test())


class TestDashboardNavigation:
    def test_templates_screen_open_and_close(self, monkeypatch):
        monkeypatch.setattr("latita.tui.list_latita_templates", lambda: {})

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("t")
                await pilot.pause()
                assert isinstance(app.screen, TemplatesScreen)
                await pilot.press("escape")
                await pilot.pause()
                assert not isinstance(app.screen, TemplatesScreen)
                await pilot.press("q")
        _run_async(_test())

    def test_capsules_screen_open_and_close(self, monkeypatch):
        monkeypatch.setattr("latita.tui.list_capsules", lambda: {})

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("p")
                await pilot.pause()
                assert isinstance(app.screen, CapsulesScreen)
                await pilot.press("escape")
                await pilot.pause()
                assert not isinstance(app.screen, CapsulesScreen)
                await pilot.press("q")
        _run_async(_test())


class TestScreensStructure:
    def test_templates_screen_bindings(self):
        screen = TemplatesScreen()
        keys = {b.key for b in screen.BINDINGS}
        assert "escape" in keys
        assert "q" in keys
        assert "tab" in keys
        assert "e" in keys
        assert "d" in keys
        assert "r" in keys
        assert "y" in keys
        assert "n" in keys

    def test_capsules_screen_bindings(self):
        screen = CapsulesScreen()
        keys = {b.key for b in screen.BINDINGS}
        assert "escape" in keys
        assert "q" in keys
        assert "tab" in keys
        assert "e" in keys
        assert "d" in keys
        assert "r" in keys
        assert "y" in keys
        assert "n" in keys

    def test_templates_screen_has_scrollable_detail(self, monkeypatch):
        monkeypatch.setattr("latita.tui.list_latita_templates", lambda: {})

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                screen = TemplatesScreen()
                await app.push_screen(screen)
                await pilot.pause()
                assert screen.query_one("#browser-detail-scroll", ScrollableContainer) is not None
                assert screen.query_one("#browser-detail", Static) is not None
                await pilot.press("escape")
                await pilot.press("q")
        _run_async(_test())

    def test_capsules_screen_has_scrollable_detail(self, monkeypatch):
        monkeypatch.setattr("latita.tui.list_capsules", lambda: {})

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                screen = CapsulesScreen()
                await app.push_screen(screen)
                await pilot.pause()
                assert screen.query_one("#browser-detail-scroll", ScrollableContainer) is not None
                assert screen.query_one("#browser-detail", Static) is not None
                await pilot.press("escape")
                await pilot.press("q")
        _run_async(_test())


class TestConfirmScreen:
    def test_confirm_screen_composes(self):
        async def _test():
            def _cb(result: bool) -> None:
                pass

            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                app.push_screen(ConfirmScreen("Are you sure?", _cb))
                await pilot.pause()
                assert isinstance(app.screen, ConfirmScreen)
                await pilot.press("n")
                await pilot.pause()
                assert not isinstance(app.screen, ConfirmScreen)
                await pilot.press("q")
        _run_async(_test())


class TestCreateVMScreen:
    def test_compose_has_widgets(self):
        screen = CreateVMScreen()
        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.push_screen(screen)
                await pilot.pause()
                assert screen.query_one("#profile", Select) is not None
                assert screen.query_one("#name", Input) is not None
                assert screen.query_one("#network_mode", Select) is not None
                assert screen.query_one("#transient", Checkbox) is not None
                assert screen.query_one("#destroy_on_stop", Checkbox) is not None
                assert screen.query_one("#btn-create", Button) is not None
                assert screen.query_one("#btn-cancel", Button) is not None
                await pilot.press("escape")
                await pilot.press("q")
        _run_async(_test())

    def test_submit_dismisses_with_recipe(self):
        screen = CreateVMScreen()
        results = []

        def _cb(result):
            results.append(result)

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.push_screen(screen, _cb)
                await pilot.pause()
                name_input = screen.query_one("#name", Input)
                name_input.value = "testvm"
                screen.action_submit()
                await pilot.pause()
                assert len(results) == 1
                assert results[0]["mode"] == "create"
                assert results[0]["recipe"]["name"] == "testvm"
                assert results[0]["recipe"]["network"]["mode"] == "nat"
                await pilot.press("q")
        _run_async(_test())

    def test_submit_isolated_network(self):
        screen = CreateVMScreen()
        results = []

        def _cb(result):
            results.append(result)

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.push_screen(screen, _cb)
                await pilot.pause()
                screen.query_one("#name", Input).value = "testvm"
                screen.query_one("#network_mode", Select).value = "isolated"
                screen.action_submit()
                await pilot.pause()
                assert len(results) == 1
                assert results[0]["recipe"]["network"]["mode"] == "isolated"
                await pilot.press("q")
        _run_async(_test())

    def test_submit_transient_and_destroy_flags(self):
        screen = CreateVMScreen()
        results = []

        def _cb(result):
            results.append(result)

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.push_screen(screen, _cb)
                await pilot.pause()
                screen.query_one("#name", Input).value = "testvm"
                screen.query_one("#transient", Checkbox).value = True
                screen.query_one("#destroy_on_stop", Checkbox).value = True
                screen.action_submit()
                await pilot.pause()
                recipe = results[0]["recipe"]
                assert recipe["ephemeral"]["transient"] is True
                assert recipe["ephemeral"]["destroy_on_stop"] is True
                await pilot.press("q")
        _run_async(_test())

    def test_submit_requires_name(self):
        screen = CreateVMScreen()
        results = []

        def _cb(result):
            results.append(result)

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.push_screen(screen, _cb)
                await pilot.pause()
                screen.action_submit()
                await pilot.pause()
                assert len(results) == 0
                error = screen.query_one("#form-error", Static)
                assert "required" in error._Static__content.lower()
                await pilot.press("escape")
                await pilot.press("q")
        _run_async(_test())


class TestRunVMScreen:
    def test_compose_has_widgets(self):
        screen = RunVMScreen()
        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.push_screen(screen)
                await pilot.pause()
                assert screen.query_one("#profile", Select) is not None
                assert screen.query_one("#name", Input) is not None
                assert screen.query_one("#network_mode", Select) is not None
                assert screen.query_one("#command", Input) is not None
                assert screen.query_one("#run-warn", Static) is not None
                assert screen.query_one("#btn-create", Button) is not None
                assert screen.query_one("#btn-cancel", Button) is not None
                await pilot.press("escape")
                await pilot.press("q")
        _run_async(_test())

    def test_submit_dismisses_with_recipe_and_command(self):
        screen = RunVMScreen()
        results = []

        def _cb(result):
            results.append(result)

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.push_screen(screen, _cb)
                await pilot.pause()
                screen.query_one("#name", Input).value = "runvm"
                screen.query_one("#command", Input).value = "uname -a"
                screen.action_submit()
                await pilot.pause()
                assert len(results) == 1
                assert results[0]["mode"] == "run"
                assert results[0]["recipe"]["name"] == "runvm"
                assert results[0]["recipe"]["command"] == "uname -a"
                assert results[0]["recipe"]["network"]["mode"] == "nat"
                await pilot.press("q")
        _run_async(_test())

    def test_submit_isolated_network(self):
        screen = RunVMScreen()
        results = []

        def _cb(result):
            results.append(result)

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.push_screen(screen, _cb)
                await pilot.pause()
                screen.query_one("#name", Input).value = "runvm"
                screen.query_one("#network_mode", Select).value = "isolated"
                screen.action_submit()
                await pilot.pause()
                assert len(results) == 1
                assert results[0]["recipe"]["network"]["mode"] == "isolated"
                await pilot.press("q")
        _run_async(_test())

    def test_submit_requires_name(self):
        screen = RunVMScreen()
        results = []

        def _cb(result):
            results.append(result)

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.push_screen(screen, _cb)
                await pilot.pause()
                screen.action_submit()
                await pilot.pause()
                assert len(results) == 0
                error = screen.query_one("#form-error", Static)
                assert "required" in error._Static__content.lower()
                await pilot.press("escape")
                await pilot.press("q")
        _run_async(_test())


class TestApplyCapsuleScreen:
    def test_compose_with_capsules(self, monkeypatch):
        monkeypatch.setattr("latita.tui.list_capsules", lambda: {"podman": {"description": "x"}})

        screen = ApplyCapsuleScreen("myvm")
        results = []

        def _cb(result):
            results.append(result)

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.push_screen(screen, _cb)
                await pilot.pause()
                assert screen.query_one("#capsule", Select) is not None
                assert screen.query_one("#btn-apply", Button) is not None
                assert screen.query_one("#btn-cancel", Button) is not None
                screen.action_submit()
                await pilot.pause()
                assert results == ["podman"]
                await pilot.press("q")
        _run_async(_test())

    def test_compose_empty_capsules(self, monkeypatch):
        monkeypatch.setattr("latita.tui.list_capsules", lambda: {})

        screen = ApplyCapsuleScreen("myvm")
        results = []

        def _cb(result):
            results.append(result)

        async def _test():
            app = Dashboard()
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.push_screen(screen, _cb)
                await pilot.pause()
                assert screen.query_one("#cap-none", Static) is not None
                assert screen.query_one("#btn-apply", Button) is not None
                assert screen.query_one("#btn-cancel", Button) is not None
                screen.action_submit()
                await pilot.pause()
                assert results == [None]
                await pilot.press("q")
        _run_async(_test())
