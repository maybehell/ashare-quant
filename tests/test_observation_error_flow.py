from pathlib import Path


def test_daily_observation_captures_native_stderr_without_terminating() -> None:
    script = Path("scripts/daily_observation.ps1").read_text(encoding="utf-8-sig")

    continue_index = script.index('$ErrorActionPreference = "Continue"')
    command_index = script.index("$result = & $aqScript @arguments 2>&1")
    restore_index = script.index(
        "$ErrorActionPreference = $previousErrorActionPreference", command_index
    )

    assert continue_index < command_index < restore_index
    assert "command_failed_without_output" in script
    assert "observation_exception_without_message" in script


def test_daily_observation_status_object_has_no_assignment_statement() -> None:
    script = Path("scripts/daily_observation.ps1").read_text(encoding="utf-8-sig")
    status_block = script.split("[PSCustomObject]@{", 2)[2].split(
        "} | Export-Csv", 1
    )[0]

    assert "$ErrorActionPreference" not in status_block

def test_alert_failure_does_not_repeat_market_data_refresh() -> None:
    script = Path("scripts/daily_observation.ps1").read_text(encoding="utf-8-sig")

    assert "daily_observation_delivery_status.csv" in script
    assert "outbox_retry_enabled = $true" in script
    assert "exit $observationExitCode" in script
    assert "exit $alertExitCode" not in script
