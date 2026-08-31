# Narrated Roadshow Video Generator

`generate_narrated_roadshow.ps1` builds a narrated review video from previously
captured screenshots. It does not call application APIs or mutate application
state.

## Requirements

- Windows PowerShell 5.1
- FFmpeg and FFprobe from the same build
- FFmpeg filters `ass` and encoder `libx264`
- An installed Windows System.Speech voice
- A UTF-8 JSON shot configuration

The PowerShell script intentionally carries a UTF-8 BOM because Windows
PowerShell 5.1 otherwise reads non-ASCII output names using the legacy system
code page.

## Usage

```powershell
.\scripts\media\generate_narrated_roadshow.ps1 `
  -ScreenshotDirectory "<screenshots>" `
  -OutputDirectory "<output>" `
  -ReportPath "<authoritative-report.md>" `
  -ApplicationId "<application-reference>" `
  -ConfigPath "<shot-config.json>" `
  -FfmpegPath "<ffmpeg.exe>" `
  -VoiceName "<installed-voice>" `
  -VoiceRate 0
```

The JSON configuration supplies the source image, ordered shots, durations,
chapter-card text and subtitle-sized narration segments. Every shot must be no
longer than 20 seconds. The generated timeline must remain between 7 minutes
and 8 minutes 30 seconds.

## Outputs

The generator creates:

- clean and burned-subtitle H.264/AAC MP4 files;
- a normalized 48kHz narration WAV;
- synchronized SRT and ASS subtitles;
- a Markdown narration script and shot list;
- a 1920x1080 cover image;
- ignored intermediate files under the output `_work` directory.

Runtime outputs can contain project-specific evidence and must remain outside
Git. Remove `_work` after acceptance.
