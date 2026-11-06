param([string]$ClipsJson, [string]$Audio, [string]$OutMp4, [int]$Bitrate = 1500000)
# Собирает MP4 (H.264 1920x1080 + AAC) средствами Windows (Windows.Media.Editing).
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "winrt.ps1")
$clips = [IO.File]::ReadAllText($ClipsJson, [Text.Encoding]::UTF8) | ConvertFrom-Json
$comp = New-Object Windows.Media.Editing.MediaComposition
foreach ($c in $clips) {
    $file = Get-StorageFile $c.Image
    $clip = Await ([Windows.Media.Editing.MediaClip]::CreateFromImageFileAsync($file, [TimeSpan]::FromSeconds([double]$c.Seconds))) ([Windows.Media.Editing.MediaClip])
    $script:AddClip.Invoke($comp.Clips, @($clip))
}
$track = Await ([Windows.Media.Editing.BackgroundAudioTrack]::CreateFromFileAsync((Get-StorageFile $Audio))) ([Windows.Media.Editing.BackgroundAudioTrack])
$script:AddTrack.Invoke($comp.BackgroundAudioTracks, @($track))
$folder = Await ([Windows.Storage.StorageFolder]::GetFolderFromPathAsync((Split-Path -Parent $OutMp4))) ([Windows.Storage.StorageFolder])
$out = Await ($folder.CreateFileAsync((Split-Path -Leaf $OutMp4), [Windows.Storage.CreationCollisionOption]::ReplaceExisting)) ([Windows.Storage.StorageFile])
$profile = [Windows.Media.MediaProperties.MediaEncodingProfile]::CreateMp4([Windows.Media.MediaProperties.VideoEncodingQuality]::HD1080p)
$profile.Video.Bitrate = [uint32]$Bitrate
$profile.Video.FrameRate.Numerator = 25
$profile.Video.FrameRate.Denominator = 1
$profile.Audio.Bitrate = [uint32]128000
$sw = [Diagnostics.Stopwatch]::StartNew()
$result = AwaitProgress ($comp.RenderToFileAsync($out, [Windows.Media.Editing.MediaTrimmingPreference]::Precise, $profile)) ([Windows.Media.Transcoding.TranscodeFailureReason]) ([double])
if ($result -ne [Windows.Media.Transcoding.TranscodeFailureReason]::None) { throw "Ошибка сборки видео: $result" }
Write-Host ("Готово за {0:N0} с: {1} ({2:N1} МБ)" -f $sw.Elapsed.TotalSeconds, $OutMp4, ((Get-Item $OutMp4).Length / 1MB))
