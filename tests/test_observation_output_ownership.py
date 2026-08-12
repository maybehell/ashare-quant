from pathlib import Path


RUNNER = Path("scripts/daily_observation.ps1")
DAEMON = Path("scripts/observation_daemon.ps1")


def test_daily_runner_separates_status_output_from_global_market_lock():
    text = RUNNER.read_text(encoding="utf-8-sig")

    assert '[string]$OutputDir = "outputs"' in text
    assert '$coordinationDir = Join-Path $projectRoot "outputs"' in text
    assert '$lockPath = Join-Path $coordinationDir "daily_observation.lock.json"' in text
    assert '$statusPath = Join-Path $outputDir "daily_observation_status.csv"' in text
    assert '$deliveryStatusPath = Join-Path $outputDir "daily_observation_delivery_status.csv"' in text


def test_observation_daemon_passes_its_owned_output_directory_to_runner():
    text = DAEMON.read_text(encoding="utf-8-sig")
    arguments = text.split('$arguments = @(', 1)[1].split(')', 1)[0]

    assert '"-OutputDir", $resolvedOutput' in arguments
