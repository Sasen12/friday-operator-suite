param(
    [ValidateSet("all", "server", "voice", "local", "playground", "console")]
    [string]$Mode = "all"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

function Get-X64PythonPath {
    $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        try {
            $platformTag = & $venvPython -c "import sysconfig; print(sysconfig.get_platform())" 2>$null
            if ($platformTag.Trim().ToLowerInvariant() -eq "win-amd64") {
                return (Resolve-Path $venvPython).Path
            }
        } catch {
            # If the existing venv is broken, fall through to the system interpreter search.
        }
    }

    $candidates = New-Object System.Collections.Generic.List[string]

    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($line in (& py -0p 2>$null)) {
            if ($line -match '(?<path>[A-Za-z]:.*python\.exe)\s*$') {
                $candidates.Add($Matches.path)
            }
        }
    }

    foreach ($path in @(
        (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    )) {
        $candidates.Add($path)
    }

    foreach ($path in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path $path)) {
            continue
        }

        try {
            $platformTag = & $path -c "import sysconfig; print(sysconfig.get_platform())" 2>$null
            if ($platformTag.Trim().ToLowerInvariant() -eq "win-amd64") {
                return (Resolve-Path $path).Path
            }
        } catch {
            continue
        }
    }

    throw "Could not find a 64-bit Python interpreter. Install Python x64 or run the project in WSL."
}

function Wait-ForPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $client = [System.Net.Sockets.TcpClient]::new()
            try {
                $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
                if ($iar.AsyncWaitHandle.WaitOne(1000, $false) -and $client.Connected) {
                    $client.EndConnect($iar)
                    return
                }
            } finally {
                $client.Close()
            }
        } catch {
            # Try again until the timeout expires.
        }

        Start-Sleep -Milliseconds 500
    }

    throw "Timed out waiting for port $Port to open."
}

function Test-PortOpen {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [int]$TimeoutMilliseconds = 500
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne($TimeoutMilliseconds, $false) -and $client.Connected) {
            $client.EndConnect($iar)
            return $true
        }
    } catch {
        return $false
    } finally {
        $client.Close()
    }

    return $false
}

function Start-FridayWindow {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WindowTitle,
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [bool]$WakeWordMode = $true
    )

    $escapedRoot = $repoRoot.Replace("'", "''")
    $escapedPython = $env:UV_PYTHON.Replace("'", "''")
    $inner = @"
`$ErrorActionPreference = 'Stop'
`$Host.UI.RawUI.WindowTitle = '$WindowTitle'
`$env:UV_PYTHON = '$escapedPython'
`$env:PYTHONUTF8 = '1'
`$env:PYTHONIOENCODING = 'utf-8'
`$env:FRIDAY_WAKE_WORD_MODE = '$(if ($WakeWordMode) { '1' } else { '0' })'
Set-Location '$escapedRoot'
$Command
"@

    Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList @(
        '-NoExit',
        '-Command',
        $inner
    ) | Out-Null
}

function Start-ServerIfNeeded {
    param(
        [int]$Port = 8000
    )

    if (Test-PortOpen -Port $Port) {
        Write-Host "Reusing existing FRIDAY server on port $Port."
        return
    }

    Start-FridayWindow -WindowTitle 'FRIDAY Server' -Command 'uv run python server.py' -WakeWordMode $false
    Wait-ForPort -Port $Port -TimeoutSeconds 60
}

$pythonPath = Get-X64PythonPath
$env:UV_PYTHON = $pythonPath
$env:FRIDAY_WAKE_WORD_MODE = '0'

Write-Host "Using Python: $pythonPath"
uv sync

switch ($Mode) {
    'server' {
        $env:FRIDAY_WAKE_WORD_MODE = '0'
        if (Test-PortOpen -Port 8000) {
            Write-Host 'Reusing existing FRIDAY server on port 8000.'
            break
        }
        uv run python server.py
    }
    'console' {
        $env:FRIDAY_WAKE_WORD_MODE = '0'
        uv run python local_friday.py console
    }
    'voice' {
        $env:FRIDAY_WAKE_WORD_MODE = '0'
        uv run python local_friday.py
    }
    'local' {
        Start-ServerIfNeeded
        Start-FridayWindow -WindowTitle 'FRIDAY Voice (Local)' -Command 'uv run python local_friday.py' -WakeWordMode $false
    }
    'playground' {
        Start-ServerIfNeeded
        Start-FridayWindow -WindowTitle 'FRIDAY Voice (Local)' -Command 'uv run python local_friday.py' -WakeWordMode $false
    }
    'all' {
        Start-ServerIfNeeded
        Start-FridayWindow -WindowTitle 'FRIDAY Voice (Local)' -Command 'uv run python local_friday.py' -WakeWordMode $false
    }
}
