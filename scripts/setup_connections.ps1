# PowerShell version of setup_connections.sh: create the nyp_ connections and load
# credentials from .env. Values still empty or <placeholder> are skipped, so run it
# again later once you have the Telegram bot or Google client.
# Usage (from anywhere):  powershell -ExecutionPolicy Bypass -File scripts\setup_connections.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".env")) { throw "Missing .env. Copy .env.example to .env and fill it in." }
$vars = @{}
foreach ($line in Get-Content ".env") {
    if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*"?(.*?)"?\s*$') { $vars[$Matches[1]] = $Matches[2] }
}
function Test-Filled([string[]]$names) {
    foreach ($n in $names) {
        $v = $vars[$n]
        if ([string]::IsNullOrWhiteSpace($v) -or $v.StartsWith("<")) { return $false }
    }
    return $true
}

if (-not (Test-Filled @("WXO_SERVER_URL", "WXO_API_KEY"))) {
    throw "WXO_SERVER_URL / WXO_API_KEY not filled in .env; cannot reach Orchestrate."
}
$orch = Join-Path $Root "venv\Scripts\orchestrate.exe"
$existing = & $orch env list 2>&1 | Out-String
if ($existing -notmatch "nyp_env") { & $orch env add -n nyp_env -u $vars["WXO_SERVER_URL"] }
& $orch env activate nyp_env --api-key $vars["WXO_API_KEY"]

foreach ($app in @("nyp_google_read", "nyp_google_act", "nyp_telegram")) {
    & $orch connections import -f (Join-Path $Root "connections\$app.yaml")
}

$GoogleScopes = @{
    "nyp_google_read" = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar.readonly"
    "nyp_google_act"  = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/calendar.events"
}
if (Test-Filled @("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")) {
    foreach ($app in @("nyp_google_read", "nyp_google_act")) {
        # access_type=offline + prompt=consent so Google returns a refresh token.
        & $orch connections set-credentials -a $app --env draft `
            --client-id $vars["GOOGLE_CLIENT_ID"] --client-secret $vars["GOOGLE_CLIENT_SECRET"] `
            --auth-url "https://accounts.google.com/o/oauth2/v2/auth" `
            --token-url "https://oauth2.googleapis.com/token" `
            --scope $GoogleScopes[$app] `
            --auth-entries "access_type=offline" --auth-entries "prompt=consent"
        if ($LASTEXITCODE -ne 0) { throw "Setting Google credentials failed for $app." }
    }
    Write-Host "Google connections set. Sign in once for each (read, then act) when Orchestrate asks."
} else {
    Write-Host "Skipped Google: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not filled."
}

if (Test-Filled @("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")) {
    & $orch connections set-credentials -a nyp_telegram --env draft --entries "bot_token=$($vars['TELEGRAM_BOT_TOKEN']),allowed_chat_id=$($vars['TELEGRAM_CHAT_ID'])"
    Write-Host "Telegram connection set."
} else {
    Write-Host "Skipped Telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not filled. Approvals will say 'telegram not configured'."
}
