# Общие функции WinRT для PowerShell 5.1: ожидание асинхронных операций, синтез речи, сборка видео.
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
[void][Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
[void][Windows.Media.Editing.MediaComposition, Windows.Media.Editing, ContentType = WindowsRuntime]
[void][Windows.Media.MediaProperties.MediaEncodingProfile, Windows.Media.MediaProperties, ContentType = WindowsRuntime]
[void][Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]

$script:Ext = [System.WindowsRuntimeSystemExtensions].GetMethods()
$script:AsTaskOp = $Ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$script:AsTaskOpProg = $Ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperationWithProgress`2' } | Select-Object -First 1
$script:AsTaskAction = $Ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1

$script:AddClip = [System.Collections.Generic.ICollection[Windows.Media.Editing.MediaClip]].GetMethod('Add')
$script:AddTrack = [System.Collections.Generic.ICollection[Windows.Media.Editing.BackgroundAudioTrack]].GetMethod('Add')

function Await($op, [Type]$type) {
    $t = $script:AsTaskOp.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}

function AwaitProgress($op, [Type]$type, [Type]$progress) {
    $t = $script:AsTaskOpProg.MakeGenericMethod($type, $progress).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}

function Get-StorageFile([string]$path) {
    Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
}

function New-Speech([string]$ssml, [string]$outWav, [string]$voiceName = 'Microsoft Pavel') {
    $synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
    $voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | Where-Object { $_.DisplayName -eq $voiceName } | Select-Object -First 1
    if (-not $voice) { throw "Голос $voiceName не найден" }
    $synth.Voice = $voice
    $stream = Await ($synth.SynthesizeSsmlToStreamAsync($ssml)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
    $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
    $size = [uint32]$stream.Size
    [void](Await ($reader.LoadAsync($size)) ([uint32]))
    $bytes = New-Object byte[] $size
    $reader.ReadBytes($bytes)
    [IO.File]::WriteAllBytes($outWav, $bytes)
    $reader.Dispose(); $stream.Dispose(); $synth.Dispose()
}

function New-Video([object[]]$clips, [string]$audioWav, [string]$outMp4) {
    # $clips: массив объектов с полями Image (путь к PNG) и Seconds (длительность)
    $comp = New-Object Windows.Media.Editing.MediaComposition
    foreach ($c in $clips) {
        $file = Get-StorageFile $c.Image
        $clip = Await ([Windows.Media.Editing.MediaClip]::CreateFromImageFileAsync($file, [TimeSpan]::FromSeconds([double]$c.Seconds))) ([Windows.Media.Editing.MediaClip])
        $script:AddClip.Invoke($comp.Clips, @($clip))
    }
    $audio = Get-StorageFile $audioWav
    $track = Await ([Windows.Media.Editing.BackgroundAudioTrack]::CreateFromFileAsync($audio)) ([Windows.Media.Editing.BackgroundAudioTrack])
    $script:AddTrack.Invoke($comp.BackgroundAudioTracks, @($track))
    $dir = Split-Path -Parent $outMp4
    $name = Split-Path -Leaf $outMp4
    $folder = Await ([Windows.Storage.StorageFolder]::GetFolderFromPathAsync($dir)) ([Windows.Storage.StorageFolder])
    $out = Await ($folder.CreateFileAsync($name, [Windows.Storage.CreationCollisionOption]::ReplaceExisting)) ([Windows.Storage.StorageFile])
    $profile = [Windows.Media.MediaProperties.MediaEncodingProfile]::CreateMp4([Windows.Media.MediaProperties.VideoEncodingQuality]::HD1080p)
    $result = AwaitProgress ($comp.RenderToFileAsync($out, [Windows.Media.Editing.MediaTrimmingPreference]::Precise, $profile)) ([Windows.Media.Transcoding.TranscodeFailureReason]) ([double])
    if ($result -ne [Windows.Media.Transcoding.TranscodeFailureReason]::None) { throw "Ошибка сборки видео: $result" }
}
