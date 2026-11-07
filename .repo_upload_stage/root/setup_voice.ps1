$ErrorActionPreference = 'Stop'
$modelName = 'vosk-model-small-en-us-0.15'
$modelUrl = "https://alphacephei.com/vosk/models/$modelName.zip"
$dataDir = Join-Path $PSScriptRoot 'data'
$targetDir = Join-Path $dataDir $modelName
$zipPath = Join-Path $env:TEMP "$modelName.zip"

if (Test-Path $targetDir) {
    Write-Host "Voice model already exists at $targetDir"
    exit 0
}

New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
Write-Host 'Downloading the official small English offline speech model (~40 MB)…'
Invoke-WebRequest -Uri $modelUrl -OutFile $zipPath
Write-Host 'Extracting model into Amigo data folder…'
Expand-Archive -LiteralPath $zipPath -DestinationPath $dataDir -Force
Remove-Item -LiteralPath $zipPath -Force

if (-not (Test-Path (Join-Path $targetDir 'am')) -and -not (Test-Path (Join-Path $targetDir 'conf'))) {
    Write-Warning "Model archive extracted, but expected model folders were not found at $targetDir. Please inspect the data folder."
    exit 1
}
Write-Host 'Done. Offline speech recognition and local wake-word detection are ready.'
