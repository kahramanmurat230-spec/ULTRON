param(
  [Parameter(Mandatory = $true)]
  [string]$ExePath
)

$ErrorActionPreference = "Stop"
$resolvedExe = (Resolve-Path $ExePath).Path
$port = 18080
$readyFile = Join-Path $env:TEMP ("ultron-desktop-e2e-" + [guid]::NewGuid().ToString() + ".json")
Remove-Item $readyFile -Force -ErrorAction SilentlyContinue

$oldPort = $env:ULTRON_PORT
$oldReady = $env:ULTRON_DESKTOP_TEST_READY_FILE
$oldExit = $env:ULTRON_DESKTOP_TEST_EXIT_AFTER_LOAD
try {
  $env:ULTRON_PORT = "$port"
  $env:ULTRON_DESKTOP_TEST_READY_FILE = $readyFile
  $env:ULTRON_DESKTOP_TEST_EXIT_AFTER_LOAD = "1"
  $process = Start-Process -FilePath $resolvedExe -PassThru

  $deadline = [DateTime]::UtcNow.AddSeconds(60)
  while (-not (Test-Path $readyFile) -and [DateTime]::UtcNow -lt $deadline) {
    if ($process.HasExited) { throw "ULTRON.exe closed before desktop readiness was reported." }
    Start-Sleep -Milliseconds 250
  }
  if (-not (Test-Path $readyFile)) { throw "Timed out waiting for ULTRON.exe desktop readiness." }

  $report = Get-Content $readyFile -Raw | ConvertFrom-Json
  if (-not $report.backendHealthy) { throw "Packaged backend health check failed: $($report.error)" }
  if (-not $report.frontendLoaded) { throw "React Cockpit did not finish loading." }

  $process.WaitForExit(30000) | Out-Null
  if (-not $process.HasExited) {
    Stop-Process -Id $process.Id -Force
    throw "ULTRON.exe did not close after the controlled E2E shutdown."
  }
  Start-Sleep -Milliseconds 500
  if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    throw "Backend still listens on $port after ULTRON.exe closed."
  }
  Write-Host "Windows desktop E2E passed: backend health, Cockpit load, and backend shutdown verified."
} finally {
  Remove-Item $readyFile -Force -ErrorAction SilentlyContinue
  $env:ULTRON_PORT = $oldPort
  $env:ULTRON_DESKTOP_TEST_READY_FILE = $oldReady
  $env:ULTRON_DESKTOP_TEST_EXIT_AFTER_LOAD = $oldExit
}
