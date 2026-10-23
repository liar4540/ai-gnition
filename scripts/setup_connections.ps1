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
. (Join-Path $PSScriptRoot "_orch.ps1")
Connect-Orch $vars["WXO_SERVER_URL"] $vars["WXO_API_KEY"]

foreach ($app in @("nyp_google_read", "nyp_google_act", "nyp_telegram")) {
    Invoke-Orch connections import -f (Join-Path $Root "connections\$app.yaml")
}

# Draft is used by Draft Preview; live is used by the deployed agent and the embedded web chat.
$Envs = @("draft", "live")

# Google: key-value connections holding a refresh token each (get them with
# scripts\get_google_refresh_token.py read / act). The tools refresh access tokens themselves.
$GoogleTokens = @{ "nyp_google_read" = "GOOGLE_READ_REFRESH_TOKEN"; "nyp_google_act" = "GOOGLE_ACT_REFRESH_TOKEN" }
foreach ($app in @("nyp_google_read", "nyp_google_act")) {
    $tokenVar = $GoogleTokens[$app]
    if (-not (Test-Filled @("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", $tokenVar))) {
        Write-Host "Skipped $app : GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / $tokenVar not filled."
        continue
    }
    foreach ($e in $Envs) {
        Invoke-Orch connections configure -a $app --env $e --type team --kind key_value
        Invoke-Orch connections set-credentials -a $app --env $e `
            -e "client_id=$($vars['GOOGLE_CLIENT_ID'])" -e "client_secret=$($vars['GOOGLE_CLIENT_SECRET'])" `
            -e "refresh_token=$($vars[$tokenVar])"
    }
    Write-Host "$app set (draft and live)."
}

if (Test-Filled @("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")) {
    foreach ($e in $Envs) {
        Invoke-Orch connections configure -a nyp_telegram --env $e --type team --kind key_value
        Invoke-Orch connections set-credentials -a nyp_telegram --env $e `
            -e "bot_token=$($vars['TELEGRAM_BOT_TOKEN'])" -e "allowed_chat_id=$($vars['TELEGRAM_CHAT_ID'])"
    }
    Write-Host "Telegram connection set (draft and live)."
} else {
    Write-Host "Skipped Telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not filled. Approvals will say 'telegram not configured'."
}
