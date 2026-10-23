# PowerShell version of deploy.sh: activate the Orchestrate env, import tools, then the agent.
# Usage (from anywhere):  powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Read-DotEnv {
    $vars = @{}
    if (-not (Test-Path ".env")) {
        throw "Missing .env. Copy .env.example to .env and fill in WXO_SERVER_URL and WXO_API_KEY."
    }
    foreach ($line in Get-Content ".env") {
        if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*"?(.*?)"?\s*$') { $vars[$Matches[1]] = $Matches[2] }
    }
    return $vars
}

function Test-Filled($vars, [string[]]$names) {
    foreach ($n in $names) {
        $v = $vars[$n]
        if ([string]::IsNullOrWhiteSpace($v) -or $v.StartsWith("<")) { return $false }
    }
    return $true
}

$envVars = Read-DotEnv
if (-not (Test-Filled $envVars @("WXO_SERVER_URL", "WXO_API_KEY"))) {
    throw "Missing WXO_SERVER_URL or WXO_API_KEY in .env (still empty or a <placeholder>). Not deploying."
}

$orch = Join-Path $Root "venv\Scripts\orchestrate.exe"
$existing = & $orch env list 2>&1 | Out-String
if ($existing -notmatch "nyp_env") { & $orch env add -n nyp_env -u $envVars["WXO_SERVER_URL"] }
& $orch env activate nyp_env --api-key $envVars["WXO_API_KEY"]
if ($LASTEXITCODE -ne 0) { throw "Could not activate nyp_env." }

# The shared tenant sometimes answers 403 "UserID does not have roles" for a moment; retry those.
function Invoke-Orch {
    # PowerShell 5.1 turns a native command's stderr into terminating errors under "Stop";
    # the CLI writes warnings and errors there, so read it as plain text instead.
    $ErrorActionPreference = "Continue"
    for ($try = 1; $try -le 4; $try++) {
        $out = & $orch @args 2>&1 | ForEach-Object { "$_" }
        $out | Write-Host
        if ($LASTEXITCODE -eq 0 -and -not ($out -match "status_code=403|does not have roles")) { return }
        if ($try -lt 4) { Write-Host "  ...Orchestrate refused (try $try of 4), retrying in 10s"; Start-Sleep -Seconds 10 }
    }
    throw "Orchestrate kept refusing: orchestrate $($args[0..1] -join ' ') (see the 403 / 'does not have roles' message above)."
}

# Only the connections listed for a file are attached to it:
# nyp_google_act (send + calendar write) goes to nyp_executor.py and nowhere else.
$AppIds = @{
    "nyp_read_mail.py"        = @("nyp_google_read")
    "nyp_diagnose.py"         = @("nyp_google_read")
    "nyp_calendar_check.py"   = @("nyp_google_read")
    "nyp_chat_views.py"       = @("nyp_google_read")
    "nyp_request_approval.py" = @("nyp_telegram", "nyp_google_read")
    "nyp_executor.py"         = @("nyp_google_act")
}

foreach ($f in Get-ChildItem "tools\nyp_*.py" | Sort-Object Name) {
    if (-not (Select-String -Path $f.FullName -Pattern '^@tool' -Quiet)) { continue }   # helper module, no tools
    $toolArgs = @("tools", "import", "-k", "python", "-f", $f.FullName, "-p", (Join-Path $Root "tools"))
    if (Test-Path "tools\requirements.txt") { $toolArgs += @("-r", (Join-Path $Root "tools\requirements.txt")) }
    if ($AppIds.ContainsKey($f.Name)) { foreach ($app in $AppIds[$f.Name]) { $toolArgs += @("-a", $app) } }
    Write-Host "Importing $($f.Name)"
    Invoke-Orch @toolArgs
}

Invoke-Orch agents import -f (Join-Path $Root "agents\nyp_inbox_agent.yaml")
Write-Host "Deployed. Open nyp_inbox_agent in Orchestrate Draft Preview and type: ping"
