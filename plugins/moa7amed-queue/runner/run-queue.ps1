<#
.SYNOPSIS
    moa7amed-queue runner: executes the queued tasks of one project, one after another.

.DESCRIPTION
    Runs outside Claude Code. For every runnable task it starts a fresh, non-interactive
    Claude Code session (`claude -p`, prompt on stdin) in the project's workspace, waits
    for it, records the result, commits the task's changes and moves on to the next task.

    Only one runner can work on a project at a time. If a runner is killed, the next one
    recovers: the interrupted task goes back in the queue.

    Works with Windows PowerShell 5.1 and PowerShell 7+ (Windows, macOS, Linux).
    Requires Python 3.8+, git and the Claude Code CLI on PATH.

.PARAMETER Project
    Project directory (default: the current directory). Its git root identifies the queue.
.PARAMETER MaxTasks
    Stop after this many tasks. 0 (default) runs until nothing is runnable.
.PARAMETER PermissionMode
    Value for `claude --permission-mode` (default: acceptEdits).
.PARAMETER Model
    Run every task on this model. Without it each task runs on the model chosen for it
    by the router (see the README: automatic model routing).
.PARAMETER RoutingMode
    Routing policy for this run: balanced, quality_first or cost_optimized.
.PARAMETER NoRouting
    Do not choose models; every task uses Claude Code's default model.
.PARAMETER AllowedTools
    Values for `claude --allowedTools`, e.g. 'Read','Edit','Bash(npm test *)'.
    A single comma-separated string also works: "Read,Edit,Bash(npm test *)".
.PARAMETER DisallowedTools
    Values for `claude --disallowedTools`.
.PARAMETER MaxBudgetUsd
    Value for `claude --max-budget-usd`, applied to each task.
.PARAMETER TimeoutMinutes
    Stop a task that runs longer than this. 0 disables the limit (default: 60).
.PARAMETER OnDirty
    What to do when the workspace has uncommitted changes before a task:
    stop (default) leaves everything untouched and exits; stash sets the changes aside
    with `git stash`; allow runs anyway (and then never auto-commits).
.PARAMETER Workspace
    inplace (default) runs in the project directory; worktree runs in a dedicated git
    worktree on its own branch so you can keep working in the project meanwhile.
.PARAMETER Commit
    auto (default) commits each successful task; none leaves changes uncommitted.
.PARAMETER ClaudeCommand
    Claude Code executable to use (default: claude).
.PARAMETER ClaudeArgs
    Extra arguments passed to every `claude` invocation, verbatim.
.PARAMETER OptionsFile
    JSON file with the settings above (snake_case keys). Explicit parameters win.
.PARAMETER Watch
    Keep running and pick up tasks as they are added.
.PARAMETER PollSeconds
    How often -Watch looks for new tasks (default: 15).
.PARAMETER StopOnFailure
    Stop as soon as a task attempt fails.
.PARAMETER DryRun
    Show what would run, start nothing.
.PARAMETER Force
    Take over the project even if another runner still looks alive.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\runner\run-queue.ps1 -Project C:\src\my-app

.EXAMPLE
    .\runner\run-queue.ps1 -Project . -Workspace worktree -AllowedTools 'Bash(npm test *)' -Watch

.NOTES
    Exit codes: 0 all tasks done, 1 a task failed or is blocked, 2 another runner holds the
    project, 3 stopped to protect uncommitted changes, 4 lock lost, 5 cost limit of the
    session reached (cost_limit_run_usd), 130 interrupted.
#>
[CmdletBinding()]
param(
    [string]$Project = (Get-Location).Path,
    [int]$MaxTasks = 0,
    [string]$PermissionMode = '',
    [string]$Model = '',
    [string]$RoutingMode = '',
    [switch]$NoRouting,
    [string[]]$AllowedTools = @(),
    [string[]]$DisallowedTools = @(),
    [double]$MaxBudgetUsd = 0,
    [double]$TimeoutMinutes = -1,
    [ValidateSet('', 'stop', 'stash', 'allow')][string]$OnDirty = '',
    [ValidateSet('', 'inplace', 'worktree')][string]$Workspace = '',
    [ValidateSet('', 'auto', 'none')][string]$Commit = '',
    [string]$ClaudeCommand = '',
    [string[]]$ClaudeArgs = @(),
    [string]$OptionsFile = '',
    [switch]$Watch,
    [int]$PollSeconds = 15,
    [switch]$StopOnFailure,
    [switch]$DryRun,
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:OnWindows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
$script:MqScript = Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts/mq.py'
$script:Utf8 = New-Object System.Text.UTF8Encoding $false
$script:LogFile = $null
$script:Child = $null
$script:RunId = $null
$script:RunnerId = $null
$HeartbeatSeconds = 30

function Write-Log {
    param([string]$Message)
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $line
    if ($script:LogFile) {
        try { [System.IO.File]::AppendAllText($script:LogFile, $line + "`n", $script:Utf8) } catch { }
    }
}

function Find-Python {
    # Returns the interpreter command as an array: executable followed by fixed arguments.
    $candidates = New-Object System.Collections.ArrayList
    if ($env:MOA7AMED_QUEUE_PYTHON) { [void]$candidates.Add(@($env:MOA7AMED_QUEUE_PYTHON)) }
    elseif ($env:CTQ_PYTHON) { [void]$candidates.Add(@($env:CTQ_PYTHON)) }   # name used before the rebrand
    if ($script:OnWindows) {
        [void]$candidates.Add(@('python')); [void]$candidates.Add(@('py', '-3')); [void]$candidates.Add(@('python3'))
    } else {
        [void]$candidates.Add(@('python3')); [void]$candidates.Add(@('python'))
    }
    $probe = 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate[0] -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $command) { continue }
        $fixed = @($candidate | Select-Object -Skip 1)
        try {
            & $command.Path @fixed -c $probe | Out-Null
            if ($LASTEXITCODE -eq 0) { return , (@($command.Path) + $fixed) }
        } catch { }
    }
    throw 'Python 3.8 or newer was not found. Install Python or set MOA7AMED_QUEUE_PYTHON to its path.'
}

function Invoke-Mq {
    # Runs a moa7amed-queue command and returns its exit code together with the parsed JSON output.
    param([string[]]$Arguments)
    $all = @($script:Python | Select-Object -Skip 1) + @($script:MqScript, '--project', $script:ProjectPath) + $Arguments
    $output = & $script:Python[0] @all
    $code = $LASTEXITCODE
    $text = (($output | Out-String)).Trim()
    $data = $null
    if ($text) { try { $data = $text | ConvertFrom-Json } catch { $data = $null } }
    return New-Object psobject -Property @{ Code = $code; Data = $data; Text = $text }
}

function Split-ToolList {
    # 'Read,Bash(a, b)' -> 'Read', 'Bash(a, b)': split on commas that are not inside parentheses.
    param([string[]]$Items)
    $result = @()
    foreach ($item in $Items) {
        if (-not $item) { continue }
        foreach ($part in [regex]::Split($item, ',(?![^()]*\))')) {
            $trimmed = $part.Trim()
            if ($trimmed) { $result += $trimmed }
        }
    }
    return , $result
}

function Resolve-Claude {
    param([string]$Name)
    if (Test-Path -LiteralPath $Name -PathType Leaf) { return (Resolve-Path -LiteralPath $Name).Path }
    $found = @(Get-Command $Name -CommandType Application -All -ErrorAction SilentlyContinue)
    if ($script:OnWindows) {
        # npm installs both "claude" (a shell script) and "claude.cmd"; only the latter can be started.
        foreach ($extension in '.exe', '.cmd', '.bat', '.com') {
            $match = $found | Where-Object { $_.Path.ToLower().EndsWith($extension) } | Select-Object -First 1
            if ($match) { return $match.Path }
        }
    }
    if ($found.Count -gt 0) { return $found[0].Path }
    throw "the Claude Code executable '$Name' was not found on PATH. Install Claude Code, or pass -ClaudeCommand with its full path."
}

function ConvertTo-NativeArgument {
    # Quote one argument for a Windows-style command line.
    param([string]$Value)
    if ($Value -ne '' -and $Value -notmatch '[\s"]') { return $Value }
    $escaped = $Value -replace '(\\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\\+)$', '$1$1'
    return '"' + $escaped + '"'
}

function Stop-ProcessTree {
    param($Process)
    if (-not $Process) { return }
    try {
        if ($script:OnWindows) {
            & cmd.exe /d /c "taskkill /PID $($Process.Id) /T /F >nul 2>&1" | Out-Null
        } else {
            try { $Process.Kill($true) } catch { $Process.Kill() }
        }
    } catch { }
}

function Complete-Run {
    # Reports how the Claude process ended and returns the queue's verdict.
    param([int]$RunId, [string[]]$Extra)
    $reply = Invoke-Mq (@('finish', '--runner-id', $script:RunnerId, '--run-id', "$RunId") + $Extra)
    if ($reply.Code -ne 0 -or -not $reply.Data) {
        $detail = $reply.Text
        if ($reply.Data -and $reply.Data.PSObject.Properties['error']) { $detail = $reply.Data.error }
        throw "could not record the result of run ${RunId}: $detail"
    }
    return $reply.Data
}

function Invoke-QueuedTask {
    # Starts Claude for one claimed task and waits for it. Returns the queue's verdict.
    param($Claim)
    try {
        $executable = Resolve-Claude $Claim.claude_bin
    } catch {
        return Complete-Run $Claim.run_id @('--exit-code', '127', '--start-error', "$($_.Exception.Message)")
    }
    $argumentLine = (@($Claim.claude_args) | ForEach-Object { ConvertTo-NativeArgument "$_" }) -join ' '
    $envNames = @()
    foreach ($property in $Claim.env.PSObject.Properties) {
        Set-Item -Path ('Env:' + $property.Name) -Value "$($property.Value)"
        $envNames += $property.Name
    }
    $start = @{
        FilePath               = $executable
        WorkingDirectory       = $Claim.cwd
        RedirectStandardInput  = $Claim.prompt_file
        RedirectStandardOutput = $Claim.stdout_file
        RedirectStandardError  = $Claim.stderr_file
        PassThru               = $true
    }
    if ($argumentLine) { $start.ArgumentList = $argumentLine }
    if ($script:OnWindows) { $start.NoNewWindow = $true }
    try {
        $process = Start-Process @start
    } catch {
        foreach ($name in $envNames) { Remove-Item -Path ('Env:' + $name) -ErrorAction SilentlyContinue }
        return Complete-Run $Claim.run_id @('--exit-code', '127', '--start-error', "could not start claude: $($_.Exception.Message)")
    }
    $null = $process.Handle   # keeps ExitCode readable after the process exits
    $script:Child = $process
    $script:RunId = $Claim.run_id
    [void](Invoke-Mq @('heartbeat', '--runner-id', $script:RunnerId, '--run-id', "$($Claim.run_id)", '--child-pid', "$($process.Id)"))

    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $lastBeat = 0
    $timedOut = $false
    while (-not $process.WaitForExit(1000)) {
        $elapsed = $watch.Elapsed.TotalSeconds
        if ($Claim.timeout_seconds -gt 0 -and $elapsed -gt $Claim.timeout_seconds) {
            $timedOut = $true
            Write-Log ("task #{0} exceeded its time limit of {1} s; stopping it" -f $Claim.task_id, $Claim.timeout_seconds)
            Stop-ProcessTree $process
            [void]$process.WaitForExit(30000)
            break
        }
        if ($elapsed - $lastBeat -ge $HeartbeatSeconds) {
            [void](Invoke-Mq @('heartbeat', '--runner-id', $script:RunnerId))
            $lastBeat = $elapsed
        }
    }
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    $script:Child = $null
    $script:RunId = $null
    foreach ($name in $envNames) { Remove-Item -Path ('Env:' + $name) -ErrorAction SilentlyContinue }

    $extra = @('--exit-code', "$exitCode")
    if ($timedOut) { $extra += '--timed-out' }
    return Complete-Run $Claim.run_id $extra
}

function Format-Result {
    param($Result)
    $head = 'task #{0} {1}' -f $Result.task_id, $Result.outcome.ToUpper()
    if ($Result.outcome -eq 'done') {
        $summary = $Result.summary
        if (-not $summary) { $summary = 'completed' }
        $text = "${head}: $summary"
        if ($Result.commit) {
            $text += ' [commit {0}, {1} file(s)]' -f $Result.commit.Substring(0, [Math]::Min(10, $Result.commit.Length)), @($Result.files_changed).Count
        }
    } else {
        $text = '{0} (attempt {1}/{2}): {3}' -f $head, $Result.attempt, $Result.max_attempts, $Result.error
        if ($Result.will_retry) { $text += ' -- will retry' }
    }
    if ($Result.actual_model) { $text += ' [ran on {0}]' -f $Result.actual_model }
    if ($Result.log_note) { $text += ' -- ' + $Result.log_note }
    return $text
}

# ----------------------------------------------------------------------------- setup

if (-not (Test-Path -LiteralPath $Project -PathType Container)) {
    Write-Host "run-queue: project directory does not exist: $Project"
    exit 1
}
$script:ProjectPath = (Resolve-Path -LiteralPath $Project).Path
if ($script:ProjectPath.Length -gt 3) { $script:ProjectPath = $script:ProjectPath.TrimEnd('\', '/') }

try {
    $script:Python = Find-Python
} catch {
    Write-Host "run-queue: $($_.Exception.Message)"
    exit 127
}

# Settings: -OptionsFile first, then explicit parameters on top.
$options = @{}
if ($OptionsFile) {
    $loaded = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $OptionsFile).Path) | ConvertFrom-Json
    foreach ($property in $loaded.PSObject.Properties) { $options[$property.Name] = $property.Value }
}
if ($PermissionMode) { $options['permission_mode'] = $PermissionMode }
if ($Model) { $options['model'] = $Model }
if ($RoutingMode) { $options['routing_mode'] = $RoutingMode }
if ($NoRouting) { $options['routing_enabled'] = $false }
$tools = Split-ToolList $AllowedTools
if ($tools.Count -gt 0) { $options['allowed_tools'] = [string[]]$tools }
$tools = Split-ToolList $DisallowedTools
if ($tools.Count -gt 0) { $options['disallowed_tools'] = [string[]]$tools }
if ($MaxBudgetUsd -gt 0) { $options['max_budget_usd'] = $MaxBudgetUsd }
if ($TimeoutMinutes -ge 0) { $options['timeout_minutes'] = $TimeoutMinutes }
if ($OnDirty) { $options['on_dirty'] = $OnDirty }
if ($Workspace) { $options['workspace'] = $Workspace }
if ($Commit) { $options['commit'] = $Commit }
if ($ClaudeCommand) { $options['claude_bin'] = $ClaudeCommand }
if ($ClaudeArgs.Count -gt 0) { $options['claude_args'] = [string[]]$ClaudeArgs }

$optionsPath = Join-Path ([System.IO.Path]::GetTempPath()) ('moa7amed-queue-options-{0}.json' -f $PID)
[System.IO.File]::WriteAllText($optionsPath, ($options | ConvertTo-Json -Depth 4), $script:Utf8)

$exitCode = 0
try {
    if ($DryRun) {
        $all = @($script:Python | Select-Object -Skip 1) + @($script:MqScript, '--project', $script:ProjectPath, 'plan', '--options-file', $optionsPath)
        & $script:Python[0] @all
        exit $LASTEXITCODE
    }

    $acquireArgs = @('runner-acquire', '--pid', "$PID", '--kind', 'powershell', '--options-file', $optionsPath)
    if ($Force) { $acquireArgs += '--force' }
    $acquired = Invoke-Mq $acquireArgs
    if ($acquired.Code -ne 0 -or -not $acquired.Data) {
        $detail = $acquired.Text
        if ($acquired.Data -and $acquired.Data.PSObject.Properties['error']) { $detail = $acquired.Data.error }
        Write-Host "run-queue: cannot start: $detail"
        if ($acquired.Code -ne 0) { exit $acquired.Code }
        exit 1
    }
    $script:RunnerId = $acquired.Data.runner_id
    $script:LogFile = Join-Path $acquired.Data.data_dir 'runner.log'
    Write-Log ("runner {0} started for {1}" -f $script:RunnerId, $acquired.Data.project)
    foreach ($item in @($acquired.Data.recovered)) {
        Write-Log $item.message
    }

    $executed = 0
    $failures = 0
    try {
        while ($true) {
            if ($MaxTasks -gt 0 -and $executed -ge $MaxTasks) {
                Write-Log "reached the limit of $MaxTasks task(s)"
                break
            }
            $reply = Invoke-Mq @('claim', '--runner-id', $script:RunnerId)
            if ($reply.Code -ne 0 -or -not $reply.Data) {
                $detail = $reply.Text
                if ($reply.Data -and $reply.Data.PSObject.Properties['error']) { $detail = $reply.Data.error }
                Write-Log "cannot continue: $detail"
                $exitCode = 1
                if ($reply.Code -ne 0) { $exitCode = $reply.Code }
                break
            }
            $claim = $reply.Data
            if ($claim.action -eq 'stop') {
                Write-Log ("STOPPED: " + $claim.message)
                $exitCode = 3
                if ($claim.PSObject.Properties['exit_code'] -and $claim.exit_code) { $exitCode = [int]$claim.exit_code }
                break
            }
            if ($claim.action -eq 'idle') {
                if ($claim.reason -eq 'backoff') {
                    Write-Log $claim.message
                    Start-Sleep -Seconds ([Math]::Min([int]$claim.wait_seconds, [Math]::Max(1, $PollSeconds)))
                    [void](Invoke-Mq @('heartbeat', '--runner-id', $script:RunnerId))
                    continue
                }
                if ($Watch) {
                    Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
                    [void](Invoke-Mq @('heartbeat', '--runner-id', $script:RunnerId))
                    continue
                }
                Write-Log ("nothing more to run: " + $claim.message)
                if ($claim.reason -eq 'blocked') { $exitCode = 1 }
                break
            }

            $budgetNote = ''
            if ($claim.PSObject.Properties['budget_note'] -and $claim.budget_note) { $budgetNote = $claim.budget_note }
            Write-Log ("task #{0} started (attempt {1}/{2}){4}{5}: {3}" -f $claim.task_id, $claim.attempt, $claim.max_attempts, $claim.title, $claim.model_note, $budgetNote)
            if ($claim.stashed) { Write-Log ("uncommitted changes were set aside first in git stash " + $claim.stashed) }
            $result = Invoke-QueuedTask $claim
            $executed++
            Write-Log (Format-Result $result)
            if ($result.PSObject.Properties['cost_warnings']) {
                foreach ($warning in @($result.cost_warnings)) {
                    if ($warning) { Write-Log ("COST WARNING: " + $warning) }
                }
            }
            if ($result.failure) {
                if ($result.status -eq 'failed') { $failures++ }
                if ($StopOnFailure) {
                    Write-Log 'stopping because a task failed (-StopOnFailure)'
                    $exitCode = 1
                    break
                }
            }
        }
        if ($failures -gt 0 -and $exitCode -eq 0) { $exitCode = 1 }
        Write-Log ("runner {0} finished: {1} run(s), {2} task(s) failed, exit code {3}" -f $script:RunnerId, $executed, $failures, $exitCode)
    } finally {
        # Also reached on Ctrl+C: stop the running task and hand the project back.
        if ($script:Child) {
            if (-not $script:Child.HasExited) {
                Write-Log 'interrupted; stopping the running task'
                Stop-ProcessTree $script:Child
                [void]$script:Child.WaitForExit(30000)
            }
            [void](Invoke-Mq @('finish', '--runner-id', $script:RunnerId, '--run-id', "$($script:RunId)", '--interrupted'))
            $exitCode = 130
        }
        [void](Invoke-Mq @('runner-release', '--runner-id', $script:RunnerId))
    }
} finally {
    Remove-Item -LiteralPath $optionsPath -ErrorAction SilentlyContinue
}
exit $exitCode
