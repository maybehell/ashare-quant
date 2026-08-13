from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_architecture_governance_documents_exist_and_are_linked() -> None:
    required = [
        ROOT / "AGENTS.md",
        ROOT / "docs" / "ARCHITECTURE.md",
        ROOT / "docs" / "ROADMAP.md",
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
        "docs/ROADMAP.md",
        "docs/DEVELOPMENT_PROCESS.md",
        "docs/DEVELOPMENT_LOG.md",
        "docs/adr/",
    ):
        assert target in agents
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for target in (
        "docs/ARCHITECTURE.md",
        "docs/ROADMAP.md",
        "docs/DEVELOPMENT_PROCESS.md",
        "docs/DEVELOPMENT_LOG.md",
        "docs/adr/README.md",
        "AGENTS.md",
    ):
        assert target in readme


def test_roadmap_is_linked_and_preserves_live_delivery_gates() -> None:
    roadmap = (ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
    process = (ROOT / "docs" / "DEVELOPMENT_PROCESS.md").read_text(encoding="utf-8")
    normalized_roadmap = " ".join(roadmap.split())
    required_phrases = [
        "no strategy portfolio above CNY 200,000",
        "60-session forward observation",
        "broker_read_only_ready=true",
        "minimum 20-session shadow soak",
        "one controlled real order of at most CNY 1,000",
        "requires a new explicit user authorization",
        "Never mark a phase complete by editing this document alone",
    ]
    for phrase in required_phrases:
        assert phrase in normalized_roadmap
    assert "docs/ROADMAP.md" in process


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
