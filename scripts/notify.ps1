# Optional Windows helper. Call only for user-authorized notification channels.
param(
    [string]$Title = "Job Hunter",
    [string]$Message = "",
    [string]$Webhook = "",
    [switch]$NoToast
)
$ErrorActionPreference = "Stop"
$result = @{ toast = "skipped"; webhook = "skipped" }
if (-not $NoToast) {
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
            [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $texts = $template.GetElementsByTagName("text")
        $texts.Item(0).AppendChild($template.CreateTextNode($Title)) | Out-Null
        $texts.Item(1).AppendChild($template.CreateTextNode($Message)) | Out-Null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
        $aumid = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aumid).Show($toast)
        $result.toast = "requested"
    }
    catch { $result.toast = "unavailable" }
}
if ($Webhook) {
    try {
        $targetUri = [uri]$Webhook
        if ($targetUri.Scheme -ne "https") { throw "HTTPS required" }
        if ($targetUri.Host -eq "sctapi.ftqq.com") {
            Invoke-RestMethod -Uri $targetUri -Method Post -Body @{ title = $Title; desp = $Message } -TimeoutSec 15 | Out-Null
        }
        elseif ($targetUri.Host -eq "api.day.app") {
            $barkUri = $Webhook.TrimEnd("/") + "/" + [uri]::EscapeDataString($Title) + "/" + [uri]::EscapeDataString($Message)
            Invoke-RestMethod -Uri $barkUri -Method Get -TimeoutSec 15 | Out-Null
        }
        else {
            $payload = @{ title = $Title; content = $Message } | ConvertTo-Json -Compress
            Invoke-RestMethod -Uri $targetUri -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 15 | Out-Null
        }
        $result.webhook = "requested"
    }
    catch {
        # Exception text may contain the webhook URL and its secret.
        $result.webhook = "failed"
    }
}
$result | ConvertTo-Json -Compress
