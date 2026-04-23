from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from latita.prompts import (
    MenuBack,
    MenuCancel,
    _run_wizard,
    ask_checkbox,
    ask_confirm,
    ask_password,
    ask_select,
    ask_text,
)


class TestExceptions:
    def test_menu_back_is_exception(self):
        assert issubclass(MenuBack, Exception)

    def test_menu_cancel_is_exception(self):
        assert issubclass(MenuCancel, Exception)


class TestAskSelectBackCancel:
    @patch("latita.prompts.questionary")
    def test_allow_back_includes_cancel_and_back(self, mock_q):
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "option"
        result = ask_select("msg", ["option"], allow_back=True)
        assert result == "option"
        args, kwargs = mock_select.call_args
        assert "← Cancel" in kwargs["choices"]
        assert "← Back" in kwargs["choices"]
        assert "option" in kwargs["choices"]

    @patch("latita.prompts.questionary")
    def test_no_allow_back_only_cancel(self, mock_q):
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "option"
        result = ask_select("msg", ["option"], allow_back=False)
        assert result == "option"
        args, kwargs = mock_select.call_args
        assert "← Cancel" in kwargs["choices"]
        assert "← Back" not in kwargs["choices"]

    @patch("latita.prompts.questionary")
    def test_back_raises_menu_back(self, mock_q):
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "← Back"
        with pytest.raises(MenuBack):
            ask_select("msg", ["option"], allow_back=True)

    @patch("latita.prompts.questionary")
    def test_cancel_raises_menu_cancel(self, mock_q):
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "← Cancel"
        with pytest.raises(MenuCancel):
            ask_select("msg", ["option"], allow_back=True)

    @patch("latita.prompts.questionary")
    def test_none_raises_menu_cancel(self, mock_q):
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = None
        with pytest.raises(MenuCancel):
            ask_select("msg", ["option"])


class TestAskTextBackCancel:
    @patch("latita.prompts.questionary")
    def test_empty_input_raises_menu_back(self, mock_q):
        mock_text = MagicMock()
        mock_q.text = mock_text
        mock_text.return_value.ask.return_value = ""
        with pytest.raises(MenuBack):
            ask_text("msg", default="", allow_back=True)

    @patch("latita.prompts.questionary")
    def test_none_raises_menu_cancel(self, mock_q):
        mock_text = MagicMock()
        mock_q.text = mock_text
        mock_text.return_value.ask.return_value = None
        with pytest.raises(MenuCancel):
            ask_text("msg")

    @patch("latita.prompts.questionary")
    def test_normal_input_returned(self, mock_q):
        mock_text = MagicMock()
        mock_q.text = mock_text
        mock_text.return_value.ask.return_value = "hello"
        result = ask_text("msg", default="", allow_back=True)
        assert result == "hello"


class TestAskConfirmBackCancel:
    @patch("latita.prompts.questionary")
    def test_confirm_with_back_uses_select(self, mock_q):
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "Yes"
        result = ask_confirm("msg", default=True, allow_back=True)
        assert result is True
        args, kwargs = mock_select.call_args
        assert "Yes" in kwargs["choices"]
        assert "No" in kwargs["choices"]
        assert "← Cancel" in kwargs["choices"]
        assert "← Back" in kwargs["choices"]

    @patch("latita.prompts.questionary")
    def test_confirm_no_back_uses_confirm(self, mock_q):
        mock_confirm = MagicMock()
        mock_q.confirm = mock_confirm
        mock_confirm.return_value.ask.return_value = True
        result = ask_confirm("msg", default=False, allow_back=False)
        assert result is True
        mock_confirm.assert_called_once()


class TestAskCheckboxBackCancel:
    @patch("latita.prompts.questionary")
    def test_back_in_checkbox_raises_menu_back(self, mock_q):
        mock_cb = MagicMock()
        mock_q.checkbox = mock_cb
        mock_cb.return_value.ask.return_value = ["← Back", "option"]
        with pytest.raises(MenuBack):
            ask_checkbox("msg", ["option"], allow_back=True)

    @patch("latita.prompts.questionary")
    def test_cancel_in_checkbox_raises_menu_cancel(self, mock_q):
        mock_cb = MagicMock()
        mock_q.checkbox = mock_cb
        mock_cb.return_value.ask.return_value = ["← Cancel"]
        with pytest.raises(MenuCancel):
            ask_checkbox("msg", ["option"], allow_back=True)

    @patch("latita.prompts.questionary")
    def test_normal_checkbox_returned(self, mock_q):
        mock_cb = MagicMock()
        mock_q.checkbox = mock_cb
        mock_cb.return_value.ask.return_value = ["option"]
        result = ask_checkbox("msg", ["option"], allow_back=True)
        assert result == ["option"]


class TestAskPasswordBackCancel:
    @patch("latita.prompts.questionary")
    def test_back_arrow_raises_menu_back(self, mock_q):
        mock_pw = MagicMock()
        mock_q.password = mock_pw
        mock_pw.return_value.ask.return_value = "←"
        with pytest.raises(MenuBack):
            ask_password("msg", allow_back=True)

    @patch("latita.prompts.questionary")
    def test_none_raises_menu_cancel(self, mock_q):
        mock_pw = MagicMock()
        mock_q.password = mock_pw
        mock_pw.return_value.ask.return_value = None
        with pytest.raises(MenuCancel):
            ask_password("msg")


class TestRunWizard:
    def test_executes_steps_forward(self):
        steps: list = [
            ("a", lambda state, back: 1),
            ("b", lambda state, back: 2),
        ]
        result = _run_wizard(steps)
        assert result == {"a": 1, "b": 2}

    def test_rewinds_on_menu_back(self):
        call_count = [0]

        def step_b(state, back):
            call_count[0] += 1
            if call_count[0] == 1:
                raise MenuBack()
            return 2

        steps: list = [
            ("a", lambda state, back: 1),
            ("b", step_b),
        ]
        result = _run_wizard(steps)
        assert result == {"a": 1, "b": 2}
        assert call_count[0] == 2

    def test_menu_back_from_step_zero_raises_cancel(self):
        def step_a(state, back):
            raise MenuBack()

        steps: list = [
            ("a", step_a),
        ]
        with pytest.raises(MenuCancel):
            _run_wizard(steps)

    def test_propagates_menu_cancel(self):
        def step_b(state, back):
            raise MenuCancel()

        steps: list = [
            ("a", lambda state, back: 1),
            ("b", step_b),
        ]
        with pytest.raises(MenuCancel):
            _run_wizard(steps)

    def test_state_passed_to_later_steps(self):
        steps: list = [
            ("a", lambda state, back: 10),
            ("b", lambda state, back: state["a"] * 2),
        ]
        result = _run_wizard(steps)
        assert result == {"a": 10, "b": 20}


class TestVmPickers:
    @patch("latita.prompts.questionary")
    @patch("latita.operations.scan_instances")
    def test_pick_vm_returns_selected_name(self, mock_scan, mock_q):
        mock_scan.return_value = [
            {"name": "vm-one", "status": "running"},
            {"name": "vm-two", "status": "shut off"},
        ]
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "vm-one (running)"
        from latita.prompts import _pick_vm

        result = _pick_vm("Select VM")
        assert result == "vm-one"

    @patch("latita.prompts.questionary")
    @patch("latita.operations.scan_instances")
    def test_pick_vm_cancel_returns_none(self, mock_scan, mock_q):
        mock_scan.return_value = [
            {"name": "vm-one", "status": "running"},
        ]
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "← Cancel"
        from latita.prompts import _pick_vm

        result = _pick_vm("Select VM")
        assert result is None

    @patch("latita.prompts.questionary")
    @patch("latita.operations.scan_instances")
    def test_pick_running_vm_filters_running(self, mock_scan, mock_q):
        mock_scan.return_value = [
            {"name": "vm-one", "status": "running"},
            {"name": "vm-two", "status": "shut off"},
        ]
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "vm-one (running)"
        from latita.prompts import _pick_running_vm

        result = _pick_running_vm("Select VM")
        assert result == "vm-one"
        args, kwargs = mock_select.call_args
        assert all("shut off" not in c for c in kwargs["choices"])

    @patch("latita.prompts.questionary")
    @patch("latita.operations.scan_instances")
    def test_pick_stopped_vm_filters_non_running(self, mock_scan, mock_q):
        mock_scan.return_value = [
            {"name": "vm-one", "status": "running"},
            {"name": "vm-two", "status": "shut off"},
        ]
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "vm-two (shut off)"
        from latita.prompts import _pick_stopped_vm

        result = _pick_stopped_vm("Select VM")
        assert result == "vm-two"
        args, kwargs = mock_select.call_args
        assert all("running" not in c for c in kwargs["choices"])

    @patch("latita.prompts.questionary")
    @patch("latita.operations.scan_instances")
    def test_pick_vm_empty_prints_warning(self, mock_scan, mock_q):
        mock_scan.return_value = []
        from latita.prompts import _pick_vm

        result = _pick_vm("Select VM")
        assert result is None

    @patch("latita.prompts.questionary")
    @patch("latita.config.list_capsules")
    def test_pick_capsule_returns_selected(self, mock_caps, mock_q):
        mock_caps.return_value = {"code-server": {}, "git-repo": {}}
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "code-server"
        from latita.prompts import _pick_capsule

        result = _pick_capsule("Select capsule")
        assert result == "code-server"

    @patch("latita.prompts.questionary")
    @patch("latita.config.list_capsules")
    def test_pick_capsule_cancel_returns_none(self, mock_caps, mock_q):
        mock_caps.return_value = {"code-server": {}}
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "← Cancel"
        from latita.prompts import _pick_capsule

        result = _pick_capsule("Select capsule")
        assert result is None


class TestPromptDownloadBaseImage:
    @patch("latita.prompts.questionary")
    @patch("latita.operations.init_base")
    def test_selects_image_and_calls_init_base(self, mock_init, mock_q):
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "Fedora 43 Cloud"
        from latita.prompts import prompt_download_base_image

        result = prompt_download_base_image()
        assert result is True
        mock_init.assert_called_once_with("fedora43-base.qcow2", "https://download.fedoraproject.org/pub/fedora/linux/releases/43/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-43-1.6.x86_64.qcow2")

    @patch("latita.prompts.questionary")
    @patch("latita.operations.init_base")
    def test_cancel_returns_false(self, mock_init, mock_q):
        mock_select = MagicMock()
        mock_q.select = mock_select
        mock_select.return_value.ask.return_value = "← Cancel"
        from latita.prompts import prompt_download_base_image

        result = prompt_download_base_image()
        assert result is False
        mock_init.assert_not_called()
