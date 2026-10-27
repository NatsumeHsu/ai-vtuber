param(
    [ValidateSet('auto', 'cpu', 'cuda')][string]$Device = 'auto',
    [switch]$NoLaunch,
    [switch]$SkipModels,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$null = New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs')
$null = New-Item -ItemType Directory -Force -Path (Join-Path $root 'TEMP/setup')
Start-Transcript -Path (Join-Path $root 'logs/setup.log') -Append | Out-Null

function Run-Python([string[]]$Arguments) {
    & $script:python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python command failed (exit $LASTEXITCODE): $($Arguments -join ' ')" }
}
function Download([string]$Url, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) { return }
    Write-Host "Downloading $Url"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile "$Destination.part"
    Move-Item -LiteralPath "$Destination.part" -Destination $Destination -Force
}
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
    $script:python = Join-Path $root '.venv/Scripts/python.exe'
    if ($CheckOnly) {
        if (-not (Test-Path -LiteralPath $python)) { throw 'No virtual environment. Run setup.bat first.' }
        Run-Python @('-X', 'utf8', 'scripts/setup_runtime.py', '--check')
    } else {
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
            $vcProcess = Start-Process -FilePath $vcInstaller -ArgumentList @('/install','/quiet','/norestart') -WindowStyle Hidden -Wait -PassThru
            if ($vcProcess.ExitCode -notin @(0,1638,3010)) { throw "C++ runtime installation failed: $($vcProcess.ExitCode)" }
            if ($vcProcess.ExitCode -eq 3010) { throw 'C++ runtime installed. Restart Windows and run setup.bat again.' }
        }
        if (-not (Test-Path -LiteralPath $python)) {
            $basePython = $null
            if (Get-Command py -ErrorAction SilentlyContinue) {
                try {
                    $candidate = & py -3.12 -c 'import sys; print(sys.executable)' 2>$null
                    if ($LASTEXITCODE -eq 0) { $basePython = [string]$candidate }
                } catch { Write-Host 'Python launcher has no usable Python 3.12.' }
            }
            if (-not $basePython -and (Get-Command python -ErrorAction SilentlyContinue)) {
                try {
                    $candidate = & python -c 'import sys; print(sys.executable if sys.version_info[:2] == (3,12) else "")' 2>$null
                    if ($LASTEXITCODE -eq 0 -and $candidate) { $basePython = [string]$candidate }
                } catch { Write-Host 'No usable Python 3.12 on PATH.' }
            }
            if (-not $basePython) {
                # Per-user install, including pip and Tcl/Tk; no administrator required.
                $target = Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312'
                $installer = Join-Path $root 'TEMP/setup/python-3.12.10-amd64.exe'
                Download 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' $installer
                $signature = Get-AuthenticodeSignature -LiteralPath $installer
                if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
                    throw 'Python installer signature verification failed.'
                }
                $process = Start-Process -FilePath $installer -ArgumentList @('/quiet', 'InstallAllUsers=0', 'Include_pip=1', 'Include_tcltk=1', 'Include_test=0', 'PrependPath=0', "TargetDir=`"$target`"") -WindowStyle Hidden -Wait -PassThru
                if ($process.ExitCode -notin @(0,3010)) { throw "Python installation failed: $($process.ExitCode)" }
                $basePython = Join-Path $target 'python.exe'
            }
            & $basePython -m venv (Join-Path $root '.venv')
            if ($LASTEXITCODE -ne 0) { throw 'Could not create virtual environment.' }
        }
        Run-Python @('-c', 'import sys,struct,tkinter; assert sys.version_info[:2]==(3,12) and struct.calcsize("P")==8, "Python 3.12 x64 required; rename incompatible .venv and retry"')
        Run-Python @('-m','pip','install','--upgrade','pip','wheel','setuptools<81')
        $useCuda = $Device -eq 'cuda'
        if ($Device -eq 'auto' -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
            & nvidia-smi --query-gpu=name --format=csv,noheader
            $useCuda = $LASTEXITCODE -eq 0
        }
        $torchChannel = if ($useCuda) { 'cu128' } else { 'cpu' }
        Run-Python @('-m','pip','install','torch==2.7.1','torchaudio==2.7.1','torchvision==0.22.1','--index-url',"https://download.pytorch.org/whl/$torchChannel")
        Run-Python @('-m','pip','install','-r','requirements-setup.txt')
        # Wheel-only install avoids requiring Visual Studio/CUDA compilation tools.
        $llamaChannel = if ($useCuda) { 'cu124' } else { 'cpu' }
        & $python -m pip install --upgrade --only-binary=llama-cpp-python llama-cpp-python --index-url "https://abetlen.github.io/llama-cpp-python/whl/$llamaChannel" --extra-index-url https://pypi.org/simple
        if ($LASTEXITCODE -ne 0 -and $useCuda -and $Device -eq 'auto') {
            Write-Host 'CUDA llama wheel unavailable. Installing CPU llama; TTS may still use CUDA.'
            Run-Python @('-m','pip','install','--only-binary=llama-cpp-python','llama-cpp-python','--index-url','https://abetlen.github.io/llama-cpp-python/whl/cpu','--extra-index-url','https://pypi.org/simple')
        } elseif ($LASTEXITCODE -ne 0) { throw 'No compatible llama-cpp-python wheel was found.' }
        Run-Python @('-m','pip','check')
        # imageio-ffmpeg is not used: its distribution does not include ffprobe.
        if (-not (Test-Path 'ffmpeg.exe') -or -not (Test-Path 'ffprobe.exe')) {
            $archive = Join-Path $root 'TEMP/setup/ffmpeg-release-essentials.zip'
            Download 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' $archive
            $unpack = Join-Path $root 'TEMP/setup/ffmpeg'
            Expand-Archive -LiteralPath $archive -DestinationPath $unpack -Force
            foreach ($name in @('ffmpeg.exe','ffprobe.exe')) {
                $found = Get-ChildItem -LiteralPath $unpack -Recurse -Filter $name | Select-Object -First 1
                if (-not $found) { throw "FFmpeg archive is missing $name" }
                if (-not (Test-Path $name)) { Copy-Item -LiteralPath $found.FullName -Destination (Join-Path $root $name) }
            }
        }
        $runtimeArgs = @('-X','utf8','scripts/setup_runtime.py','--device',$Device)
        if ($SkipModels) { $runtimeArgs += '--skip-models' }
        Run-Python $runtimeArgs
    }
    Write-Host 'Setup and checks completed.'
    Stop-Transcript | Out-Null
    if (-not $NoLaunch -and -not $CheckOnly) {
        # Foreground launch keeps startup exceptions visible.
        & $python -X utf8 (Join-Path $root 'vtuberAI.py')
        exit $LASTEXITCODE
    }
    exit 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Stop-Transcript | Out-Null
    exit 1
}
