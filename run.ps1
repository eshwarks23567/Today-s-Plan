# Start Today's Plan and expose it over a Cloudflare quick tunnel.
#
#   .\run.ps1            server + public tunnel URL
#   .\run.ps1 -LocalOnly server only, http://localhost:8765
#
# The server binds loopback either way; cloudflared reaches it from this machine.
# Quick tunnels hand out a NEW random URL every start — fine for "send me the
# link", annoying if you installed the PWA to your home screen. A named tunnel
# (needs a Cloudflare account and a domain) is what gives you a stable address.
param([switch]$LocalOnly)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"

if (-not $env:GEMINI_API_KEY) {
    Write-Warning "GEMINI_API_KEY is not set — the app will crawl listings but every answer will fail."
}

Write-Host "starting server on http://localhost:8765 ..."
$server = Start-Process python -ArgumentList "server.py" -WorkingDirectory "$root\backend" `
                        -WindowStyle Hidden -PassThru

# wait for it to actually answer rather than guessing at a sleep duration
$up = $false
foreach ($i in 1..40) {
    Start-Sleep -Milliseconds 250
    try { Invoke-WebRequest "http://localhost:8765/" -TimeoutSec 2 -UseBasicParsing | Out-Null; $up = $true; break } catch {}
}
if (-not $up) { Write-Error "server did not come up"; exit 1 }
Write-Host "server ready (pid $($server.Id))" -ForegroundColor Green

if ($LocalOnly) {
    Write-Host "`n  http://localhost:8765`n"
    Write-Host "Ctrl+C here stops nothing — stop the server with: Stop-Process -Id $($server.Id)"
    exit 0
}

if (-not (Test-Path $cloudflared)) {
    Write-Error "cloudflared not found at $cloudflared — install it or use -LocalOnly"
    exit 1
}
$log = Join-Path $env:TEMP "todaysplan-tunnel.log"
Remove-Item $log -ErrorAction SilentlyContinue
$tunnel = Start-Process $cloudflared -ArgumentList "tunnel","--url","http://localhost:8765","--no-autoupdate" `
                        -WindowStyle Hidden -PassThru -RedirectStandardError $log -RedirectStandardOutput "$log.out"

$url = $null
foreach ($i in 1..60) {
    Start-Sleep -Milliseconds 500
    if (Test-Path $log) {
        $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue
        if ($m) { $url = $m.Matches[0].Value; break }
    }
}
if ($url) {
    Write-Host "`n  $url`n" -ForegroundColor Cyan
    Write-Host "Anyone with that link can use it and spend your Gemini quota."
} else {
    Write-Warning "tunnel started but no URL appeared; check $log"
}
Write-Host "stop both with:  Stop-Process -Id $($server.Id),$($tunnel.Id)"
