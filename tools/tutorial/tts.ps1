param([string]$Dir)
# Озвучивает все *.ssml в папке голосом Microsoft Pavel (пропускает уже готовые .wav).
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "winrt.ps1")
$files = Get-ChildItem $Dir -Filter *.ssml | Sort-Object Name
foreach ($f in $files) {
    $wav = [IO.Path]::ChangeExtension($f.FullName, ".wav")
    if (Test-Path $wav) { continue }
    $ssml = [IO.File]::ReadAllText($f.FullName, [Text.Encoding]::UTF8)
    New-Speech $ssml $wav
    Write-Host "Озвучено: $($f.Name)"
}
