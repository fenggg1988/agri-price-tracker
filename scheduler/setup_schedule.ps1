param(
    [switch]$Remove
)

$TaskName = "AgriPriceTracker"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$BatPath = Join-Path $ProjectDir "run_scraper.bat"

if ($Remove) {
    Write-Host "Removing scheduled task: $TaskName"
    schtasks /Delete /TN $TaskName /F
    Write-Host "Task removed."
    exit 0
}

# 每天北京时间 09:00 执行
$Trigger = New-ScheduledTaskTrigger -Daily -At 09:00
$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BatPath`"" -WorkingDirectory $ProjectDir
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Trigger $Trigger -Action $Action -Principal $Principal -Settings $Settings -Description "农化(化肥/农药)价格每日抓取" -Force

Write-Host "Scheduled task '$TaskName' created successfully."
Write-Host "Runs daily at 09:00 Beijing time."
Write-Host ""
Write-Host "To test immediately, run: $BatPath"
Write-Host "To remove: .\setup_schedule.ps1 -Remove"
