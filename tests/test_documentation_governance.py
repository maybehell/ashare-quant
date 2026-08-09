from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_architecture_governance_documents_exist_and_are_linked() -> None:
    required = [
        ROOT / "AGENTS.md",
        ROOT / "docs" / "ARCHITECTURE.md",
        ROOT / "docs" / "DEVELOPMENT_PROCESS.md",
        ROOT / "docs" / "DEVELOPMENT_LOG.md",
        ROOT / "docs" / "adr" / "README.md",
        ROOT / "docs" / "adr" / "0001-evidence-gated-layered-architecture.md",
    ]
    for path in required:
        assert path.is_file(), f"missing governance document: {path.relative_to(ROOT)}"
        assert path.read_text(encoding="utf-8").strip(), path

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for target in (
        "docs/ARCHITECTURE.md",
        "docs/DEVELOPMENT_PROCESS.md",
        "docs/DEVELOPMENT_LOG.md",
        "docs/adr/",
    ):
        assert target in agents
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for target in (
        "docs/ARCHITECTURE.md",
        "docs/DEVELOPMENT_PROCESS.md",
        "docs/DEVELOPMENT_LOG.md",
        "docs/adr/README.md",
        "AGENTS.md",
    ):
        assert target in readme


def test_architecture_contains_required_live_safety_boundaries() -> None:
    architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    required_phrases = [
        "Fail closed",
        "Research modules never call broker or RPA modules",
        "Read-only refresh cannot submit orders or initialize the live ledger",
        "allow_broker_submit=false",
        "A timed-out submit is an unknown outcome until queried",
        "Forward evidence is based only on dates later than the freeze/evidence date",
    ]
    for phrase in required_phrases:
        assert phrase in architecture


def test_development_log_has_current_entry_and_verification_section() -> None:
    log = (ROOT / "docs" / "DEVELOPMENT_LOG.md").read_text(encoding="utf-8")
    assert "## 2026-08-09" in log
    assert "**Verification**" in log
    assert "**Remaining work**" in log
