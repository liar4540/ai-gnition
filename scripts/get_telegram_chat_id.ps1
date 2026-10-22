# Print the chat id(s) that have messaged your bot, using TELEGRAM_BOT_TOKEN from .env.
# First send your bot any message (e.g. "hi") in Telegram, then run:
#   powershell -ExecutionPolicy Bypass -File scripts\get_telegram_chat_id.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$token = $null
foreach ($line in Get-Content ".env") {
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN\s*=\s*"?(.*?)"?\s*$') { $token = $Matches[1] }
}
if (-not $token -or $token.StartsWith("<")) {
    throw "TELEGRAM_BOT_TOKEN is not filled in .env. Get it from @BotFather first."
}

$base = "https://api.telegram.org/bot$token"
try {
    $me = Invoke-RestMethod "$base/getMe"
} catch {
    throw "Telegram rejected the token. Copy it again from @BotFather (format 123456789:ABC...)."
}
Write-Host "Bot: @$($me.result.username)"

# A webhook blocks getUpdates; this bot only uses polling, so remove any webhook.
$hook = Invoke-RestMethod "$base/getWebhookInfo"
if ($hook.result.url) {
    Invoke-RestMethod "$base/deleteWebhook" | Out-Null
    Write-Host "Removed an old webhook so updates can be read."
}

$updates = (Invoke-RestMethod "$base/getUpdates").result
$chats = @{}
foreach ($u in $updates) {
    $msg = if ($u.message) { $u.message } elseif ($u.callback_query) { $u.callback_query.message } else { $null }
    if ($msg) { $chats["$($msg.chat.id)"] = "$($msg.chat.first_name) $($msg.chat.username)".Trim() }
}
if ($chats.Count -eq 0) {
    Write-Host "No messages yet. Open @$($me.result.username) in Telegram, press Start or send 'hi', then run this again."
} else {
    foreach ($id in $chats.Keys) { Write-Host "chat id: $id   ($($chats[$id]))" }
    Write-Host "Put your own chat id in .env as TELEGRAM_CHAT_ID=""<id>"""
}
