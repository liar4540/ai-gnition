# PowerShell version of deploy.sh: activate the Orchestrate env, import tools, then the agent.
# Usage (from anywhere):  powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 [-Live]
#   -Live  also deploys the agent to the live environment (needed for the embedded web chat).
param([switch]$Live)
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

. (Join-Path $PSScriptRoot "_orch.ps1")
Connect-Orch $envVars["WXO_SERVER_URL"] $envVars["WXO_API_KEY"]

# Only the connections listed for a file are attached to it:
# nyp_google_act (send + calendar write) goes to nyp_executor.py and nowhere else.
$AppIds = @{
    "nyp_read_mail.py"        = @("nyp_google_read")
    "nyp_diagnose.py"         = @("nyp_google_read")
    "nyp_calendar_check.py"   = @("nyp_google_read")
    "nyp_chat_views.py"       = @("nyp_google_read")
    "nyp_request_approval.py" = @("nyp_telegram", "nyp_google_read")
    "nyp_save_analysis.py"    = @("nyp_telegram", "nyp_google_read")
    "nyp_executor.py"         = @("nyp_google_act", "nyp_telegram")
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
if ($Live) {
    Invoke-Orch agents deploy -n nyp_inbox_agent
    Write-Host "Agent deployed to live. Next: scripts\get_web_config.ps1 for the web dashboard."
}
Write-Host "Deployed. Open nyp_inbox_agent in Orchestrate Draft Preview and type: ping"
