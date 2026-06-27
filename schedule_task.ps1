$ErrorActionPreference = "Stop"

$TaskName = "DouyinStreakRenewal"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VbsPath = Join-Path $ScriptDir "run_silent.vbs"
$ConfigPath = Join-Path $ScriptDir "config.json"

$ScheduleHour = 9
$ScheduleMinute = 0
if (Test-Path -LiteralPath $ConfigPath) {
    $Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -ne $Config.schedule) {
        if ($null -ne $Config.schedule.hour) {
            $ScheduleHour = [int]$Config.schedule.hour
        }
        if ($null -ne $Config.schedule.minute) {
            $ScheduleMinute = [int]$Config.schedule.minute
        }
    }
}

if ($ScheduleHour -lt 0 -or $ScheduleHour -gt 23) {
    throw "schedule.hour must be between 0 and 23"
}
if ($ScheduleMinute -lt 0 -or $ScheduleMinute -gt 59) {
    throw "schedule.minute must be between 0 and 59"
}

$TriggerTime = (Get-Date).Date.AddHours($ScheduleHour).AddMinutes($ScheduleMinute)

if (-not (Test-Path -LiteralPath $VbsPath)) {
    throw "Silent launcher not found: $VbsPath"
}

$Action = New-ScheduledTaskAction `
    -Execute "$env:SystemRoot\System32\wscript.exe" `
    -Argument "`"$VbsPath`"" `
    -WorkingDirectory $ScriptDir

$Trigger = New-ScheduledTaskTrigger -Daily -At $TriggerTime

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5)

$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description ("Daily {0:HH:mm} Douyin streak renewal (silent, headless)" -f $TriggerTime) `
    -Force | Out-Null

Write-Output "TASK_CREATED=$TaskName"
Write-Output ("RUN_AT={0:HH:mm}" -f $TriggerTime)
