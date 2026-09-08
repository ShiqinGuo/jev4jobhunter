# Compatibility entry point. Portable checks live inside the standalone Skill.
# DataDir is retained for old callers; deduplication now belongs to store.py begin.
param(
    [string]$Text = "",
    [string]$TextFile = "",
    [ValidateSet("L1", "L2", "L3")][string]$Level = "L2",
    [string]$Thread = "",
    [string]$DataDir = "",
    [int]$MinLen = 1,
    [int]$MaxLen = 800,
    [string[]]$AllowedLinks = @(),
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}
$checker = Join-Path $PSScriptRoot "..\skills\job-hunter\scripts\audit_gate.py"
if ($MaxLen -eq 0) { $MaxLen = 800 }
$cliArgs = @("-B", "-X", "utf8", $checker, "--level", $Level, "--thread=$Thread", "--min-len", $MinLen, "--max-len", $MaxLen)
if ($TextFile) { $cliArgs += "--text-file=$TextFile" }
else { $cliArgs += "--text=$Text" }
foreach ($approvedLink in $AllowedLinks) { $cliArgs += "--allow-link=$approvedLink" }
try {
    & $Python @cliArgs
    exit $LASTEXITCODE
}
catch {
    Write-Output '{"pass":false,"error":"python-unavailable-or-launch-failed"}'
    exit 3
}
