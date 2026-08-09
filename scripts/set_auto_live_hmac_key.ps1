param(
    [string]$EnvironmentVariable = "ASHARE_QUANT_ADAPTER_HMAC_KEY",
    [switch]$PersistUser,
    [switch]$Rotate
)

$ErrorActionPreference = "Stop"
$existing = [Environment]::GetEnvironmentVariable($EnvironmentVariable, "Process")
if (-not $existing -and $PersistUser) {
    $existing = [Environment]::GetEnvironmentVariable($EnvironmentVariable, "User")
}

if ($existing -and -not $Rotate) {
    Write-Host "$EnvironmentVariable already exists. Use -Rotate to replace it."
    exit 0
}

$bytes = New-Object byte[] 48
$generator = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $generator.GetBytes($bytes)
} finally {
    $generator.Dispose()
}
$secret = [Convert]::ToBase64String($bytes)
[Environment]::SetEnvironmentVariable($EnvironmentVariable, $secret, "Process")

if ($PersistUser) {
    [Environment]::SetEnvironmentVariable($EnvironmentVariable, $secret, "User")
    Write-Host "$EnvironmentVariable generated and stored for the current Windows user. Restart scheduled tasks and terminals before certification."
} else {
    Write-Host "$EnvironmentVariable generated for this PowerShell process only. Child processes launched from this shell will inherit it."
}
Write-Host "The secret value was not printed or written to project files."
