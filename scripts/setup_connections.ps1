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
$existing = & $orch env list 2>&1 | Out-String
if ($existing -notmatch "nyp_env") { & $orch env add -n nyp_env -u $vars["WXO_SERVER_URL"] }
& $orch env activate nyp_env --api-key $vars["WXO_API_KEY"]

foreach ($app in @("nyp_google_read", "nyp_google_act", "nyp_telegram")) {
    Invoke-Orch connections import -f (Join-Path $Root "connections\$app.yaml")
}

# Google: key-value connections holding a refresh token each (get them with
# scripts\get_google_refresh_token.py read / act). The tools refresh access tokens themselves.
$GoogleTokens = @{ "nyp_google_read" = "GOOGLE_READ_REFRESH_TOKEN"; "nyp_google_act" = "GOOGLE_ACT_REFRESH_TOKEN" }
foreach ($app in @("nyp_google_read", "nyp_google_act")) {
    $tokenVar = $GoogleTokens[$app]
    if (-not (Test-Filled @("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", $tokenVar))) {
        Write-Host "Skipped $app : GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / $tokenVar not filled."
        continue
    }
    Invoke-Orch connections configure -a $app --env draft --type team --kind key_value
    Invoke-Orch connections set-credentials -a $app --env draft `
        -e "client_id=$($vars['GOOGLE_CLIENT_ID'])" -e "client_secret=$($vars['GOOGLE_CLIENT_SECRET'])" `
        -e "refresh_token=$($vars[$tokenVar])"
    Write-Host "$app set."
}

if (Test-Filled @("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")) {
    Invoke-Orch connections set-credentials -a nyp_telegram --env draft `
        -e "bot_token=$($vars['TELEGRAM_BOT_TOKEN'])" -e "allowed_chat_id=$($vars['TELEGRAM_CHAT_ID'])"
    Write-Host "Telegram connection set."
} else {
    Write-Host "Skipped Telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not filled. Approvals will say 'telegram not configured'."
}
