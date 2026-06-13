# Installer script for Windows.
$ErrorActionPreference = "Stop"

# Find Python 3.10+.
[string[]]$python = $null
foreach ($c in @("py", "python")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) {
        [string[]]$candidate = if ($c -eq "py") { @("py", "-3") } else { @("python") }
        $oldErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            if ($candidate.Count -gt 1) {
                & $candidate[0] $candidate[1] -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" *> $null
            } else {
                & $candidate[0] -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" *> $null
            }
            if ($LASTEXITCODE -eq 0) {
                $python = $candidate
                $ErrorActionPreference = $oldErrorAction
                break
            }
        } catch {}
        $ErrorActionPreference = $oldErrorAction
    }
}
if (-not $python) { Write-Error "Python 3.10+ is required but was not found."; exit 1 }

# Safe wrapper to call Python.
function Run-Python {
    $argsToPass = @()
    if ($python.Count -gt 1) {
        $argsToPass += $python[1..($python.Count - 1)]
    }
    $argsToPass += $args
    & $python[0] @argsToPass
}

# Ensure user install directory is on PATH.
$userBase = Run-Python -m site --user-base 2>$null
if ($userBase) { $env:PATH = "$userBase\Scripts;$HOME\.local\bin;$env:PATH" }

# Run local AMX command line interface.
function Invoke-AmxCli {
    if (Get-Command amx -ErrorAction SilentlyContinue) {
        & amx @args
    } else {
        Run-Python -m amx.cli @args
    }
}

# Check for existing installation.
$amxCmd       = Get-Command amx -ErrorAction SilentlyContinue
$isInstalled  = $null -ne $amxCmd
$installedVer = if ($isInstalled) {
    (& amx version 2>$null | Select-Object -First 1)
} else { $null }
if ($isInstalled -and -not $installedVer) { $installedVer = "(installed)" }

if ($isInstalled) {
    $ans = Read-Host "AMX $installedVer is already installed. Update it? [Y/n]"
    if ($ans -match '^[Nn]') { Write-Host "Keeping existing version. Nothing changed."; exit 0 }
} else {
    $ans = Read-Host "AMX is not installed. Install it now? [Y/n]"
    if ($ans -match '^[Nn]') { Write-Host "Cancelled."; exit 0 }
}

# Perform installation.
# Select install source.
$source = if ($env:AMX_SOURCE) {
    $env:AMX_SOURCE
} elseif ((Test-Path "pyproject.toml") -and
          (Select-String -Path "pyproject.toml" -Pattern 'name = "amx"' -Quiet)) {
    "."
} else { "git+https://github.com/Mr-T-443/Agent-Memory-Exchange.git" }

# Choose install method.
$havePipx = [bool](Get-Command pipx -ErrorAction SilentlyContinue)
Run-Python -m venv --help *> $null
$haveVenv = ($LASTEXITCODE -eq 0)
$venvDir  = Join-Path $HOME ".amx-venv"

$method = $env:AMX_INSTALL_METHOD
if (-not $method) {
    if ($havePipx -and $haveVenv) {
        Write-Host ""
        Write-Host "Two install methods are available:"
        Write-Host "  1. pipx  - isolated, managed for you (recommended)"
        Write-Host "  2. venv  - a dedicated virtualenv at $venvDir (no pipx needed)"
        $m = Read-Host "Which? [1]"
        $method = if ($m -match '^(2|venv)$') { "venv" } else { "pipx" }
    } elseif ($havePipx) { $method = "pipx" }
    elseif ($haveVenv)   { $method = "venv" }
}

Write-Host ""
if ($method -eq "pipx") {
    Write-Host "Installing amx from '$source' with pipx ..."
    & pipx install --force $source
    if ($LASTEXITCODE -ne 0) { Write-Error "Install failed."; exit 1 }
} elseif ($method -eq "venv") {
    Write-Host "Creating a dedicated virtualenv at $venvDir ..."
    Run-Python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { Write-Error "Could not create the virtualenv."; exit 1 }
    $venvPy  = Join-Path $venvDir "Scripts\python.exe"
    $venvAmx = Join-Path $venvDir "Scripts\amx.exe"
    & $venvPy -m pip install --quiet --upgrade pip
    Write-Host "Installing amx from '$source' into the venv ..."
    & $venvPy -m pip install --upgrade $source
    if ($LASTEXITCODE -ne 0) { Write-Error "Install failed."; exit 1 }
    # Add dedicated virtualenv Scripts directory to user PATH.
    $scripts = Join-Path $venvDir "Scripts"
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$scripts*") {
        [Environment]::SetEnvironmentVariable("Path", "$scripts;$userPath", "User")
    }
    $env:PATH = "$scripts;$env:PATH"
    # Use local venv executable.
    function Invoke-AmxCli { & $venvAmx @args }
} else {
    Write-Host "Installing amx from '$source' ..."
    Run-Python -m pip install --upgrade $source
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "No pipx and no venv module, and pip couldn't install AMX."
        Write-Host "Install pipx, then re-run this installer:"
        Write-Host "  py -m pip install --user pipx"
        Write-Host "  py -m pipx ensurepath"
        Write-Host "Or install AMX directly:  pipx install $source"
        exit 1
    }
}

# Ensure local bin is on PATH.
if ($userBase) { $env:PATH = "$userBase\Scripts;$HOME\.local\bin;$env:PATH" }

Write-Host ""
Write-Host "amx installed:"
Invoke-AmxCli info

# Set up Foundry IQ grounding.
Write-Host ""
$ans = Read-Host "Connect Foundry IQ (Azure AI Search) for grounded retrieval? [y/N]"
if ($ans -match '^[Yy]') {
    $endpoint = Read-Host "  Azure AI Search endpoint (https://<service>.search.windows.net)"
    $apiKey   = Read-Host "  API key"
    $index    = Read-Host "  Index name [amx-memory]"
    if (-not $index) { $index = "amx-memory" }

    if (-not $endpoint -or -not $apiKey) {
        Write-Host "Endpoint and API key are required. Skipping Foundry IQ setup."
        Write-Host "Run 'amx enable-foundry' later to set it up."
    } else {
        # Save credentials to ~/.amx/.env.
        $envDir  = Join-Path $HOME ".amx"
        $envFile = Join-Path $envDir ".env"
        New-Item -ItemType Directory -Force $envDir | Out-Null

        function Set-EnvFileLine($file, $key, $value) {
            $lines = if (Test-Path $file) { Get-Content $file } else { @() }
            $found = $false
            $out   = $lines | ForEach-Object {
                if ($_ -match "^$key=") { "$key=$value"; $found = $true } else { $_ }
            }
            if (-not $found) { $out += "$key=$value" }
            $out | Set-Content $file -Encoding UTF8
        }
        Set-EnvFileLine $envFile "AMX_FOUNDRY_IQ_ENDPOINT" $endpoint
        Set-EnvFileLine $envFile "AMX_FOUNDRY_IQ_API_KEY"   $apiKey
        Set-EnvFileLine $envFile "AMX_FOUNDRY_IQ_INDEX"      $index

        Write-Host ""
        Write-Host "Credentials saved. Testing connection and enabling sync..."
        Invoke-AmxCli enable-foundry
    }
} else {
    Write-Host "Skipped. Run 'amx enable-foundry' later to add Foundry IQ."
}

# Register AMX in AI clients.
Write-Host ""
$ans = Read-Host "Register AMX in your AI clients (Claude Code, Cursor, Codex, ...)? [Y/n]"
if ($ans -match '^[Nn]') {
    Write-Host "Skipped. Run 'amx install-mcp' anytime."
} else {
    Invoke-AmxCli install-mcp
}

# Print next steps.
Write-Host @"

Next steps:
  1. Restart your AI client so it picks up the AMX server.
     (Skipped registration? Run: amx install-mcp)
  2. Tell your assistant: "set up AMX"
  3. Full guide: docs/WORKING.md
"@
