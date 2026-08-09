from pathlib import Path

from scripts.git_sync_preflight import audit, path_problem


def test_generated_and_sensitive_paths_are_rejected() -> None:
    assert path_problem("data/state.sqlite3")
    assert path_problem("outputs/report.html")
    assert path_problem("outputs_candidate/equity_curve.csv")
    assert path_problem("Logger/github_sync.log")
    assert path_problem("configs/.env.live")
    assert path_problem("certificates/client.pfx")


def test_source_paths_are_allowed() -> None:
    assert path_problem("ashare_quant/backtest.py") is None
    assert path_problem("configs/default.yaml") is None
    assert path_problem("docs/ARCHITECTURE.md") is None
    assert path_problem("tests/test_rules.py") is None


def test_secret_content_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.py"
    fake_token = "ghp_" + "123456789012345678901234567890"
    source.write_text(f"TOKEN = '{fake_token}'", encoding="utf-8")
    assert audit(tmp_path, [source.name]) == [f"{source.name}: GitHub token"]


def test_normal_source_content_passes(tmp_path: Path) -> None:
    source = tmp_path / "safe.py"
    source.write_text("def value():\n    return 42\n", encoding="utf-8")
    assert audit(tmp_path, [source.name]) == []

def test_sync_script_is_allowlisted_and_non_destructive() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts" / "sync_github.ps1").read_text(
        encoding="utf-8"
    )
    assert '".gitattributes"' in script
    assert '"ashare_quant"' in script
    assert '"configs"' in script
    assert '"data"' not in script
    assert "git add -A" not in script
    assert '"push", "origin", $Branch' in script
    assert "--force" not in script
