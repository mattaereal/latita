from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from latita.cli import app

runner = CliRunner()


class TestCliHelp:
    def test_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "latita" in result.output.lower()

    def test_create_help(self):
        result = runner.invoke(app, ["create", "--help"])
        assert result.exit_code == 0
        assert "template" in result.output.lower()

    def test_capsule_list(self):
        result = runner.invoke(app, ["capsule", "list"])
        assert result.exit_code == 0
        assert "code-server" in result.output

    def test_template_list(self):
        result = runner.invoke(app, ["template", "list"])
        assert result.exit_code == 0
        assert "headless" in result.output

    def test_doctor(self):
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "uri" in result.output

    def test_template_show(self):
        result = runner.invoke(app, ["template", "show", "headless"])
        assert result.exit_code == 0
        assert "headless" in result.output

    def test_template_show_missing(self):
        result = runner.invoke(app, ["template", "show", "nonexistent"])
        assert result.exit_code != 0

    def test_run_help(self):
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "ephemeral" in result.output.lower()

    def test_template_generate_help(self):
        result = runner.invoke(app, ["template", "generate", "--help"])
        assert result.exit_code == 0
        assert "output" in result.output.lower()


class TestMenuStructure:
    def test_menu_loop_importable(self):
        from latita.prompts import menu_loop
        assert callable(menu_loop)

    def test_submenu_helper_exists(self):
        from latita.prompts import _submenu
        assert callable(_submenu)

    def test_menu_loop_signature(self):
        import inspect
        from latita.prompts import menu_loop
        sig = inspect.signature(menu_loop)
        params = list(sig.parameters.keys())
        assert "on_create" in params
        assert "on_run" in params
        assert "on_list" in params
        assert "on_start" in params
        assert "on_stop" in params
        assert "on_destroy" in params
        assert "on_ssh" in params
        assert "on_connect" in params
        assert "on_capsule_apply" in params
        assert "on_bootstrap" in params
        assert "on_doctor" in params

    def test_picker_helpers_importable(self):
        from latita.prompts import _pick_vm, _pick_running_vm, _pick_stopped_vm, _pick_capsule, prompt_download_base_image
        assert callable(_pick_vm)
        assert callable(_pick_running_vm)
        assert callable(_pick_stopped_vm)
        assert callable(_pick_capsule)
        assert callable(prompt_download_base_image)


class TestEnsureRunning:
    @patch("latita.cli.start_instance")
    @patch("latita.cli.scan_instances")
    def test_starts_stopped_vm_and_prints_disclaimer(self, mock_scan, mock_start):
        from latita.cli import _ensure_running

        mock_scan.return_value = [{"name": "vm1", "status": "shut off"}]
        _ensure_running("vm1")
        mock_start.assert_called_once_with("vm1")

    @patch("latita.cli.start_instance")
    @patch("latita.cli.scan_instances")
    def test_skips_already_running_vm(self, mock_scan, mock_start):
        from latita.cli import _ensure_running

        mock_scan.return_value = [{"name": "vm1", "status": "running"}]
        _ensure_running("vm1")
        mock_start.assert_not_called()


class TestMenuActions:
    @patch("latita.cli.start_instance")
    @patch("latita.cli._pick_stopped_vm")
    def test_menu_start(self, mock_pick, mock_start):
        from latita.cli import _menu_start

        mock_pick.return_value = "vm1"
        _menu_start()
        mock_start.assert_called_once_with("vm1")

    @patch("latita.cli.stop_instance")
    @patch("latita.cli._pick_running_vm")
    def test_menu_stop(self, mock_pick, mock_stop):
        from latita.cli import _menu_stop

        mock_pick.return_value = "vm1"
        _menu_stop()
        mock_stop.assert_called_once_with("vm1")

    @patch("latita.cli.destroy_instance")
    @patch("latita.cli.typer.confirm")
    @patch("latita.cli._pick_vm")
    def test_menu_destroy_confirmed(self, mock_pick, mock_confirm, mock_destroy):
        from latita.cli import _menu_destroy

        mock_pick.return_value = "vm1"
        mock_confirm.return_value = True
        _menu_destroy()
        mock_destroy.assert_called_once_with("vm1")

    @patch("latita.cli.destroy_instance")
    @patch("latita.cli.typer.confirm")
    @patch("latita.cli._pick_vm")
    def test_menu_destroy_cancelled(self, mock_pick, mock_confirm, mock_destroy):
        from latita.cli import _menu_destroy

        mock_pick.return_value = "vm1"
        mock_confirm.return_value = False
        _menu_destroy()
        mock_destroy.assert_not_called()

    @patch("latita.cli.ssh_instance")
    @patch("latita.cli._ensure_running")
    @patch("latita.cli._pick_vm")
    @patch("latita.cli._pick_running_vm")
    def test_menu_ssh_fallback_to_stopped(self, mock_run, mock_all, mock_ensure, mock_ssh):
        from latita.cli import _menu_ssh

        mock_run.return_value = None  # no running VMs
        mock_all.return_value = "vm1"  # user picks a stopped VM
        _menu_ssh()
        mock_ensure.assert_called_once_with("vm1")
        mock_ssh.assert_called_once_with("vm1")

    @patch("latita.cli.connect_instance")
    @patch("latita.cli._ensure_running")
    @patch("latita.cli._pick_vm")
    @patch("latita.cli._pick_running_vm")
    def test_menu_connect_fallback_to_stopped(self, mock_run, mock_all, mock_ensure, mock_conn):
        from latita.cli import _menu_connect

        mock_run.return_value = None
        mock_all.return_value = "vm1"
        _menu_connect()
        mock_ensure.assert_called_once_with("vm1")
        mock_conn.assert_called_once_with("vm1")

    @patch("latita.cli.apply_capsule_live")
    @patch("latita.cli._pick_capsule")
    @patch("latita.cli._ensure_running")
    @patch("latita.cli._pick_vm")
    @patch("latita.cli._pick_running_vm")
    def test_menu_capsule_fallback_to_stopped(self, mock_run, mock_all, mock_ensure, mock_cap, mock_apply):
        from latita.cli import _menu_capsule_apply

        mock_run.return_value = None
        mock_all.return_value = "vm1"
        mock_cap.return_value = "code-server"
        _menu_capsule_apply()
        mock_ensure.assert_called_once_with("vm1")
        mock_apply.assert_called_once_with("vm1", "code-server")
