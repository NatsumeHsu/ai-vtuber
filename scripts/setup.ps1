param(
    [ValidateSet('auto','cpu','cuda')][string]$Device='auto',
    [switch]$NoLaunch,
    [switch]$SkipModels,
    [switch]$CheckOnly
)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$null=New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs')
Start-Transcript -Path (Join-Path $root 'logs/setup.log') -Append | Out-Null
$uvCommand=Get-Command uv -ErrorAction SilentlyContinue
$uv=if($uvCommand){$uvCommand.Source}else{Join-Path $env:LOCALAPPDATA 'Microsoft/WinGet/Links/uv.exe'}
if(-not(Test-Path -LiteralPath $uv)){throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'}
function Run-Uv([string[]]$Arguments){ & $uv @Arguments; if($LASTEXITCODE -ne 0){throw "uv failed: $($Arguments -join ' ')"} }
$python=Join-Path $root '.venv/Scripts/python.exe'
if(-not $CheckOnly){
    Run-Uv @('python','install','3.11')
    if(-not(Test-Path -LiteralPath $python)){Run-Uv @('venv','--python','3.11','.venv')}
    & $python -c 'import sys; assert sys.version_info[:2]==(3,11), "Python 3.11 required; move the old .venv before setup"'
    if($LASTEXITCODE -ne 0){throw 'Incompatible Python environment'}
    Run-Uv @('pip','sync','requirements.txt','--python',$python,'--extra-index-url','https://download.pytorch.org/whl/cu128','--index-strategy','unsafe-best-match')
    # All speech, camera and LLM dependencies share the project .venv.
    if(-not(Test-Path -LiteralPath (Join-Path $root 'index-tts/indextts/infer_v2_5.py'))){throw 'Missing IndexTTS 2.5 source in index-tts'}
}
& $python -X utf8 scripts/prepare_index_tts.py
if($LASTEXITCODE -ne 0){throw 'IndexTTS compatibility preparation failed'}
& $python -X utf8 scripts/setup_runtime.py --check --device $Device
if($LASTEXITCODE -ne 0){throw 'Runtime checks failed'}
Stop-Transcript | Out-Null
if(-not $NoLaunch -and -not $CheckOnly){ & $python -X utf8 vtuberAI.py }
