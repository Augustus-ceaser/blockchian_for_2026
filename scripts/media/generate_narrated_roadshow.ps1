[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ScreenshotDirectory,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$ReportPath,

    [Parameter(Mandatory = $true)]
    [string]$ApplicationId,

    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,

    [string]$FfmpegPath = "ffmpeg",
    [string]$VoiceName = "Microsoft Huihui Desktop",
    [ValidateRange(-10, 10)]
    [int]$VoiceRate = -1
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
}

function ConvertTo-SrtTime {
    param([double]$Seconds)
    $time = [TimeSpan]::FromSeconds($Seconds)
    return "{0:00}:{1:00}:{2:00},{3:000}" -f [math]::Floor($time.TotalHours), $time.Minutes, $time.Seconds, $time.Milliseconds
}

function ConvertTo-AssTime {
    param([double]$Seconds)
    $time = [TimeSpan]::FromSeconds($Seconds)
    return "{0}:{1:00}:{2:00}.{3:00}" -f [math]::Floor($time.TotalHours), $time.Minutes, $time.Seconds, [math]::Floor($time.Milliseconds / 10)
}

function Split-SubtitleLine {
    param([string]$Text)
    if ($Text.Length -le 22) {
        return $Text
    }
    if ($Text.Length -gt 44) {
        throw "Subtitle exceeds 44 characters: $Text"
    }
    $split = [math]::Ceiling($Text.Length / 2)
    for ($i = $split; $i -ge [math]::Max(6, $split - 4); $i--) {
        if ("，。；：、 ".Contains($Text[$i - 1])) {
            $split = $i
            break
        }
    }
    if (($Text.Length - $split) -lt 6) {
        $split = $Text.Length - 6
    }
    return $Text.Substring(0, $split) + "`n" + $Text.Substring($split)
}

function Escape-ConcatPath {
    param([string]$Path)
    return $Path.Replace("'", "'\''")
}

function Get-MediaDuration {
    param(
        [string]$Ffprobe,
        [string]$Path
    )
    $value = & $Ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $Path
    if ($LASTEXITCODE -ne 0) {
        throw "ffprobe failed for $Path"
    }
    return [double]::Parse(($value | Select-Object -First 1), [Globalization.CultureInfo]::InvariantCulture)
}

function New-CardImage {
    param(
        [string]$SourcePath,
        [string]$DestinationPath,
        [string]$Title,
        [string]$Subtitle,
        [bool]$IsCover,
        [string[]]$FooterLines
    )

    Add-Type -AssemblyName System.Drawing
    $source = [System.Drawing.Image]::FromFile($SourcePath)
    try {
        $bitmap = New-Object System.Drawing.Bitmap 1920, 1080
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.DrawImage($source, 0, 0, 1920, 1080)
            $graphics.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(205, 8, 25, 38))), 0, 0, 1920, 1080)

            $accent = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 17, 170, 165))
            $white = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
            $muted = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(225, 220, 231, 238))
            try {
                $graphics.FillRectangle($accent, 180, 312, 120, 8)
                $titleSize = if ($IsCover) { 76 } else { 62 }
                $titleFont = New-Object System.Drawing.Font("Microsoft YaHei", $titleSize, [System.Drawing.FontStyle]::Bold)
                $subtitleFont = New-Object System.Drawing.Font("Microsoft YaHei", 32, [System.Drawing.FontStyle]::Regular)
                $footerFont = New-Object System.Drawing.Font("Microsoft YaHei", 23, [System.Drawing.FontStyle]::Regular)
                try {
                    $graphics.DrawString($Title, $titleFont, $white, 180, 350)
                    $graphics.DrawString($Subtitle, $subtitleFont, $muted, 184, 470)
                    if ($FooterLines.Count -gt 0) {
                        for ($lineIndex = 0; $lineIndex -lt $FooterLines.Count; $lineIndex++) {
                            $lineY = if ($IsCover -and $lineIndex -eq 0) {
                                580
                            }
                            elseif ($IsCover) {
                                910 + (($lineIndex - 1) * 34)
                            }
                            else {
                                910 + ($lineIndex * 34)
                            }
                            $graphics.DrawString($FooterLines[$lineIndex], $footerFont, $muted, 184, $lineY)
                        }
                    }
                }
                finally {
                    $titleFont.Dispose()
                    $subtitleFont.Dispose()
                    $footerFont.Dispose()
                }
            }
            finally {
                $accent.Dispose()
                $white.Dispose()
                $muted.Dispose()
            }
            $bitmap.Save($DestinationPath, [System.Drawing.Imaging.ImageFormat]::Png)
        }
        finally {
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    }
    finally {
        $source.Dispose()
    }
}

$ScreenshotDirectory = (Resolve-Path -LiteralPath $ScreenshotDirectory).Path
$ReportPath = (Resolve-Path -LiteralPath $ReportPath).Path
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path
$workDirectory = Join-Path $OutputDirectory "_work"
$audioDirectory = Join-Path $workDirectory "audio"
$cardDirectory = Join-Path $workDirectory "cards"
New-Item -ItemType Directory -Force -Path $workDirectory, $audioDirectory, $cardDirectory | Out-Null

if (-not (Test-Path -LiteralPath $FfmpegPath -PathType Leaf)) {
    $ffmpegCommand = Get-Command $FfmpegPath -ErrorAction Stop
    $FfmpegPath = $ffmpegCommand.Source
}
$ffprobePath = Join-Path (Split-Path -Parent $FfmpegPath) "ffprobe.exe"
if (-not (Test-Path -LiteralPath $ffprobePath -PathType Leaf)) {
    throw "ffprobe.exe was not found next to ffmpeg.exe"
}

$encoders = & $FfmpegPath -hide_banner -encoders 2>&1
if (-not ($encoders -match "\blibx264\b")) {
    throw "The selected FFmpeg build does not provide libx264"
}
$filters = & $FfmpegPath -hide_banner -filters 2>&1
if (-not ($filters -match "\bass\s+V->V")) {
    throw "The selected FFmpeg build does not provide the ASS subtitle filter"
}

$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $config.shots -or $config.shots.Count -eq 0) {
    throw "The configuration contains no shots"
}

Add-Type -AssemblyName System.Speech
$synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $availableVoice = $synthesizer.GetInstalledVoices() |
        Where-Object { $_.Enabled -and $_.VoiceInfo.Name -eq $VoiceName } |
        Select-Object -First 1
    if (-not $availableVoice) {
        throw "The requested installed voice is unavailable: $VoiceName"
    }
    $synthesizer.SelectVoice($VoiceName)
    $synthesizer.Rate = $VoiceRate
    $waveFormat = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
        48000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono
    )

    $baseImage = Join-Path $ScreenshotDirectory $config.base_image
    if (-not (Test-Path -LiteralPath $baseImage -PathType Leaf)) {
        throw "Base screenshot not found: $baseImage"
    }

    $resolvedShots = @()
    $subtitleEvents = @()
    $audioConcatLines = New-Object System.Collections.Generic.List[string]
    $videoConcatLines = New-Object System.Collections.Generic.List[string]
    $timeline = 0.0
    $subtitleIndex = 0

    foreach ($shot in $config.shots) {
        $duration = [double]$shot.duration_seconds
        if ($duration -le 0 -or $duration -gt 20) {
            throw "Shot duration must be greater than zero and no more than 20 seconds: $($shot.sequence)"
        }

        if ($shot.type -eq "card") {
            $imagePath = Join-Path $cardDirectory ("{0}.png" -f $shot.sequence)
            $footerLines = @()
            if ($shot.PSObject.Properties.Name -contains "card_footer") {
                $footerLines = [string[]]$shot.card_footer
            }
            New-CardImage -SourcePath $baseImage -DestinationPath $imagePath -Title $shot.card_title -Subtitle $shot.card_subtitle -IsCover ([bool]$shot.is_cover) -FooterLines $footerLines
        }
        else {
            $imagePath = Join-Path $ScreenshotDirectory $shot.filename
            if (-not (Test-Path -LiteralPath $imagePath -PathType Leaf)) {
                throw "Screenshot not found: $imagePath"
            }
        }

        $shotStart = $timeline
        $cursor = 0.30
        $leadPath = Join-Path $audioDirectory ("lead_{0}.wav" -f $shot.sequence)
        Invoke-Native -Command $FfmpegPath -Arguments @(
            "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", "0.30", "-c:a", "pcm_s16le", $leadPath
        )
        $audioConcatLines.Add("file '$(Escape-ConcatPath $leadPath)'")
        $shotNarration = New-Object System.Collections.Generic.List[string]
        foreach ($subtitle in $shot.subtitles) {
            $subtitleIndex++
            $audioPath = Join-Path $audioDirectory ("subtitle_{0:D3}.wav" -f $subtitleIndex)
            $synthesizer.SetOutputToWaveFile($audioPath, $waveFormat)
            $synthesizer.Speak([string]$subtitle)
            $synthesizer.SetOutputToNull()

            $audioDuration = Get-MediaDuration -Ffprobe $ffprobePath -Path $audioPath
            $eventStart = $shotStart + $cursor
            $eventEnd = $eventStart + $audioDuration
            $subtitleEvents += [pscustomobject]@{
                Index = $subtitleIndex
                Start = $eventStart
                End = $eventEnd
                Text = [string]$subtitle
            }
            $shotNarration.Add([string]$subtitle)

            $audioConcatLines.Add("file '$(Escape-ConcatPath $audioPath)'")
            $cursor += $audioDuration
            $gap = 0.25
            $gapPath = Join-Path $audioDirectory ("gap_{0:D3}.wav" -f $subtitleIndex)
            Invoke-Native -Command $FfmpegPath -Arguments @(
                "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                "-t", $gap.ToString("0.###", [Globalization.CultureInfo]::InvariantCulture),
                "-c:a", "pcm_s16le", $gapPath
            )
            $audioConcatLines.Add("file '$(Escape-ConcatPath $gapPath)'")
            $cursor += $gap
        }

        $duration = [math]::Max($cursor + 0.20, [math]::Min($duration, $cursor + 2.50))
        if ($duration -gt 20) {
            throw "Narration requires a shot longer than 20 seconds at $($shot.sequence)"
        }
        $videoConcatLines.Add("file '$(Escape-ConcatPath $imagePath)'")
        $videoConcatLines.Add("duration $($duration.ToString('0.###', [Globalization.CultureInfo]::InvariantCulture))")
        $padding = $duration - $cursor
        $paddingPath = Join-Path $audioDirectory ("padding_{0}.wav" -f $shot.sequence)
        Invoke-Native -Command $FfmpegPath -Arguments @(
            "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", $padding.ToString("0.###", [Globalization.CultureInfo]::InvariantCulture),
            "-c:a", "pcm_s16le", $paddingPath
        )
        $audioConcatLines.Add("file '$(Escape-ConcatPath $paddingPath)'")

        $resolvedShots += [pscustomobject]@{
            Sequence = [string]$shot.sequence
            Start = $shotStart
            End = $shotStart + $duration
            Duration = $duration
            Image = $imagePath
            Filename = if ($shot.type -eq "card") { [System.IO.Path]::GetFileName($imagePath) } else { [string]$shot.filename }
            Stage = [string]$shot.business_stage
            Role = [string]$shot.role
            Action = [string]$shot.screen_action
            Highlight = [string]$shot.highlight
            Narration = ($shotNarration -join "")
            Subtitle = ($shot.subtitles -join "")
            Transition = "hard cut with chapter cards"
        }
        $timeline += $duration
    }

    if ($timeline -lt 420 -or $timeline -gt 510) {
        throw "Final timeline must be between 420 and 510 seconds; actual duration is $timeline"
    }
    $videoConcatLines.Add("file '$(Escape-ConcatPath $($resolvedShots[-1].Image))'")

    $videoConcatPath = Join-Path $workDirectory "video.concat.txt"
    $audioConcatPath = Join-Path $workDirectory "audio.concat.txt"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($videoConcatPath, [string[]]$videoConcatLines, $utf8NoBom)
    [System.IO.File]::WriteAllLines($audioConcatPath, [string[]]$audioConcatLines, $utf8NoBom)

    $srtPath = Join-Path $OutputDirectory "MedTrust_Space_青创杯完整演示.srt"
    $assPath = Join-Path $OutputDirectory "MedTrust_Space_青创杯完整演示.ass"
    $srtLines = New-Object System.Collections.Generic.List[string]
    foreach ($event in $subtitleEvents) {
        $srtLines.Add([string]$event.Index)
        $srtLines.Add("$(ConvertTo-SrtTime $event.Start) --> $(ConvertTo-SrtTime $event.End)")
        $srtLines.Add((Split-SubtitleLine $event.Text))
        $srtLines.Add("")
    }
    Set-Content -LiteralPath $srtPath -Value $srtLines -Encoding UTF8

    $assLines = New-Object System.Collections.Generic.List[string]
    $assLines.Add("[Script Info]")
    $assLines.Add("ScriptType: v4.00+")
    $assLines.Add("PlayResX: 1920")
    $assLines.Add("PlayResY: 1080")
    $assLines.Add("WrapStyle: 2")
    $assLines.Add("ScaledBorderAndShadow: yes")
    $assLines.Add("")
    $assLines.Add("[V4+ Styles]")
    $assLines.Add("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
    $assLines.Add("Style: Default,Microsoft YaHei,46,&H00FFFFFF,&H00FFFFFF,&H00101010,&H78000000,0,0,0,0,100,100,0,0,3,2,0,2,100,100,65,1")
    $assLines.Add("")
    $assLines.Add("[Events]")
    $assLines.Add("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")
    foreach ($event in $subtitleEvents) {
        $assText = (Split-SubtitleLine $event.Text).Replace("`n", "\N")
        $assLines.Add("Dialogue: 0,$(ConvertTo-AssTime $event.Start),$(ConvertTo-AssTime $event.End),Default,,0,0,0,,$assText")
    }
    Set-Content -LiteralPath $assPath -Value $assLines -Encoding UTF8

    $narrationPath = Join-Path $OutputDirectory "青创杯完整演示解说稿.md"
    $narrationLines = New-Object System.Collections.Generic.List[string]
    $narrationLines.Add("# 青创杯完整演示解说稿")
    $narrationLines.Add("")
    $narrationLines.Add("- Application: ``$ApplicationId``")
    $narrationLines.Add("- Source report: ``$([System.IO.Path]::GetFileName($ReportPath))``")
    $narrationLines.Add("- Boundary: PathMNIST public demonstration data; non-clinical engineering MVP; ``hard_isolation=false``.")
    $narrationLines.Add("")
    foreach ($shot in $resolvedShots) {
        $narrationLines.Add("## $(ConvertTo-AssTime $shot.Start)-$(ConvertTo-AssTime $shot.End) $($shot.Stage)")
        $narrationLines.Add("")
        $narrationLines.Add("画面：$($shot.Action)")
        $narrationLines.Add("")
        $narrationLines.Add("解说：$($shot.Narration)")
        $narrationLines.Add("")
        $narrationLines.Add("字幕：$($shot.Subtitle)")
        $narrationLines.Add("")
    }
    Set-Content -LiteralPath $narrationPath -Value $narrationLines -Encoding UTF8

    $storyboardPath = Join-Path $OutputDirectory "青创杯完整演示分镜表.md"
    $storyboardLines = New-Object System.Collections.Generic.List[string]
    $storyboardLines.Add("# 青创杯完整演示分镜表")
    $storyboardLines.Add("")
    $storyboardLines.Add("| 镜头编号 | 起始时间 | 结束时间 | 时长 | 截图文件 | 业务阶段 | 角色 | 画面动作 | 局部高亮 | 解说文本 | 字幕文本 | 转场方式 | 验收状态 |")
    $storyboardLines.Add("|---|---:|---:|---:|---|---|---|---|---|---|---|---|---|")
    foreach ($shot in $resolvedShots) {
        $storyboardLines.Add("| $($shot.Sequence) | $(ConvertTo-AssTime $shot.Start) | $(ConvertTo-AssTime $shot.End) | $($shot.Duration) | $($shot.Filename) | $($shot.Stage) | $($shot.Role) | $($shot.Action) | $($shot.Highlight) | $($shot.Narration) | $($shot.Subtitle) | $($shot.Transition) | pending |")
    }
    Set-Content -LiteralPath $storyboardPath -Value $storyboardLines -Encoding UTF8

    $rawNarrationPath = Join-Path $workDirectory "narration_raw.wav"
    $narrationPath = Join-Path $OutputDirectory "MedTrust_Space_青创杯完整解说.wav"
    Invoke-Native -Command $FfmpegPath -Arguments @(
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", $audioConcatPath,
        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", $rawNarrationPath
    )
    Invoke-Native -Command $FfmpegPath -Arguments @(
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", $rawNarrationPath,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=5:linear=false",
        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", $narrationPath
    )

    $silentVideoPath = Join-Path $workDirectory "silent_video.mp4"
    Invoke-Native -Command $FfmpegPath -Arguments @(
        "-hide_banner", "-loglevel", "warning", "-y",
        "-f", "concat", "-safe", "0", "-i", $videoConcatPath,
        "-vf", "scale=1920:1080:flags=lanczos,format=yuv420p",
        "-r", "30", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", $silentVideoPath
    )

    $cleanVideoPath = Join-Path $OutputDirectory "MedTrust_Space_青创杯完整演示_1080p_无字幕.mp4"
    Invoke-Native -Command $FfmpegPath -Arguments @(
        "-hide_banner", "-loglevel", "warning", "-y",
        "-i", $silentVideoPath, "-i", $narrationPath,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", $timeline.ToString("0.###", [Globalization.CultureInfo]::InvariantCulture),
        "-movflags", "+faststart", $cleanVideoPath
    )

    $burnedVideoPath = Join-Path $OutputDirectory "MedTrust_Space_青创杯完整演示_1080p_有字幕.mp4"
    $assFilterPath = $assPath.Replace("\", "/").Replace(":", "\:")
    Invoke-Native -Command $FfmpegPath -Arguments @(
        "-hide_banner", "-loglevel", "warning", "-y",
        "-i", $cleanVideoPath,
        "-vf", "ass='$assFilterPath'",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", $burnedVideoPath
    )

    Copy-Item -LiteralPath (Join-Path $cardDirectory "$($config.shots[0].sequence).png") -Destination (Join-Path $OutputDirectory "视频封面.png") -Force

    [pscustomobject]@{
        ApplicationId = $ApplicationId
        DurationSeconds = $timeline
        ShotCount = $resolvedShots.Count
        SubtitleCount = $subtitleEvents.Count
        Voice = $VoiceName
        CleanVideo = $cleanVideoPath
        BurnedVideo = $burnedVideoPath
        Narration = $narrationPath
        Srt = $srtPath
        Ass = $assPath
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $workDirectory "generation-result.json") -Encoding UTF8
}
finally {
    $synthesizer.Dispose()
}
