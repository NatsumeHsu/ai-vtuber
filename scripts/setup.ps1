param(
    [ValidateSet('auto', 'cpu', 'cuda')][string]$Device = 'auto',
    [switch]$NoLaunch,
    # Models are supplied locally; retained for setup.bat compatibility.
    [switch]$SkipModels,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv/Scripts/python.exe'
Set-Location -LiteralPath $root
$null = New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs')

function Run-Uv([string[]]$Arguments) {
    & $uv @Arguments
    if ($LASTEXITCODE -ne 0) { throw "uv failed: $($Arguments -join ' ')" }
}
function Run-Python([string[]]$Arguments) {
    & $python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python command failed (exit ${LASTEXITCODE}): $($Arguments -join ' ')" }
}
function Download([string]$Url, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) { return }
    $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination)
    Write-Host "Downloading $Url"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile "$Destination.part"
    Move-Item -LiteralPath "$Destination.part" -Destination $Destination -Force
}

$exitCode = 0
$transcriptStarted = $false
try {
    Start-Transcript -Path (Join-Path $root 'logs/setup.log') -Append | Out-Null
    $transcriptStarted = $true
    if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
    if ($CheckOnly) {
        if (-not (Test-Path -LiteralPath $python)) { throw 'No virtual environment. Run setup.bat first.' }
        Run-Python @('-X', 'utf8', 'scripts/setup_runtime.py', '--check')
    }
    else {
        $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
        $uv = if ($uvCommand) { $uvCommand.Source } else { Join-Path $env:LOCALAPPDATA 'Microsoft/WinGet/Links/uv.exe' }
        if (-not (Test-Path -LiteralPath $uv)) { throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/' }
        if (-not (Test-Path -LiteralPath (Join-Path $root 'index-tts/indextts/infer_v2_5.py'))) {
            throw 'Missing IndexTTS 2.5 source in index-tts'
        }
        # PyTorch, ONNX Runtime and native wheels require the Microsoft C++ runtime.
        $vcRuntime = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
        if (-not $vcRuntime -or $vcRuntime.Installed -ne 1) {
            $vcInstaller = Join-Path $root 'TEMP/setup/vc_redist.x64.exe'
            Download 'https://aka.ms/vs/17/release/vc_redist.x64.exe' $vcInstaller
            $vcSignature = Get-AuthenticodeSignature -LiteralPath $vcInstaller
            if ($vcSignature.Status -ne 'Valid' -or $vcSignature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
                throw 'Microsoft C++ runtime signature verification failed.'
            }
            Write-Host 'Installing Microsoft C++ runtime. Windows may request administrator approval.'
            $vcProcess = Start-Process -FilePath $vcInstaller -ArgumentList @('/install', '/quiet', '/norestart') -WindowStyle Hidden -Wait -PassThru
            if ($vcProcess.ExitCode -notin @(0, 1638, 3010)) { throw "C++ runtime installation failed: $($vcProcess.ExitCode)" }
            if ($vcProcess.ExitCode -eq 3010) { throw 'C++ runtime installed. Restart Windows and run setup.bat again.' }
        }
        Run-Uv @('python', 'install', '3.11')
        if (-not (Test-Path -LiteralPath $python)) { Run-Uv @('venv', '--python', '3.11', '.venv') }
        # Single quotes inside Python code survive Windows PowerShell argument passing.
        Run-Python @('-c', 'import sys,struct,tkinter; assert sys.version_info[:2]==(3,11) and struct.calcsize(''P'')==8, ''Python 3.11 x64 required; move the old .venv before setup''')
        Run-Uv @('pip', 'sync', 'requirements.txt', '--python', $python, '--extra-index-url', 'https://download.pytorch.org/whl/cu128', '--index-strategy', 'unsafe-best-match')
        Run-Uv @('pip', 'check', '--python', $python)
        # imageio-ffmpeg is not used: its distribution does not include ffprobe.
        if (-not (Test-Path 'ffmpeg.exe') -or -not (Test-Path 'ffprobe.exe')) {
            $archive = Join-Path $root 'TEMP/setup/ffmpeg-release-essentials.zip'
            Download 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' $archive
            $unpack = Join-Path $root 'TEMP/setup/ffmpeg'
            Expand-Archive -LiteralPath $archive -DestinationPath $unpack -Force
            foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
                $found = Get-ChildItem -LiteralPath $unpack -Recurse -Filter $name | Select-Object -First 1
                if (-not $found) { throw "FFmpeg archive is missing $name" }
                if (-not (Test-Path $name)) { Copy-Item -LiteralPath $found.FullName -Destination (Join-Path $root $name) }
            }
        }
        Run-Python @('-X', 'utf8', 'scripts/prepare_index_tts.py')
        Run-Python @('-X', 'utf8', 'scripts/setup_runtime.py', '--check', '--device', $Device)
    }
    Write-Host 'Setup and checks completed.'
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}
finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
}
if ($exitCode -eq 0 -and -not $NoLaunch -and -not $CheckOnly) {
    & $python -X utf8 (Join-Path $root 'vtuberAI.py')
    $exitCode = $LASTEXITCODE
}
exit $exitCode
