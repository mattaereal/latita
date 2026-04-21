from __future__ import annotations

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
