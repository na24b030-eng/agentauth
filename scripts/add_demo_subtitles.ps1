param(
    [string]$VideoPath = "artifacts/agentauth-five-minute-demo.mp4"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$videoFile = Join-Path $projectRoot $VideoPath
$temporaryVideo = Join-Path $projectRoot "artifacts/agentauth-five-minute-demo-subtitled.mp4"
$narrationPath = Join-Path $projectRoot "docs/DEMO_NARRATION.txt"
$referenceAudio = Join-Path $projectRoot "artifacts/agentauth-subtitle-reference.mp3"
$rawSubtitles = Join-Path $projectRoot "artifacts/agentauth-demo-narration.vtt"
$shortSubtitles = Join-Path $projectRoot "artifacts/agentauth-demo-subtitles.vtt"
$ffmpeg = Join-Path $projectRoot "node_modules/ffmpeg-static/ffmpeg.exe"
$uv = Get-Command "uv" -ErrorAction SilentlyContinue

if (-not (Test-Path -LiteralPath $videoFile)) { throw "Demo video not found: $videoFile" }
if (-not (Test-Path -LiteralPath $ffmpeg)) { throw "FFmpeg is missing. Run: npm install" }
if (-not $uv) { throw "uv is required to generate subtitle timings" }

& $uv.Source run --with edge-tts edge-tts `
    --voice "en-IN-NeerjaNeural" `
    --rate "+0%" `
    --file $narrationPath `
    --write-media $referenceAudio `
    --write-subtitles $rawSubtitles
if ($LASTEXITCODE -ne 0) { throw "Subtitle timing generation failed" }

node (Join-Path $projectRoot "scripts/shorten_subtitles.mjs") $rawSubtitles $shortSubtitles
if ($LASTEXITCODE -ne 0) { throw "Subtitle shortening failed" }

Push-Location $projectRoot
try {
    # Preserve the entire 1280x720 recording and place captions in a slim safe strip
    # below it, rather than covering any product UI.
    $subtitleStyle = "FontName=Segoe UI,FontSize=10,PrimaryColour=&H00FFFFFF,BackColour=&H80000000,BorderStyle=3,Outline=2,Shadow=0,MarginV=6,Alignment=2"
    & $ffmpeg -y -i $videoFile `
        -map 0:v:0 -map 0:a:0 `
        -vf "pad=iw:ih+36:0:0:color=0x111418,subtitles=artifacts/agentauth-demo-subtitles.vtt:force_style='$subtitleStyle'" `
        -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p `
        -c:a copy -movflags +faststart $temporaryVideo
    if ($LASTEXITCODE -ne 0) { throw "FFmpeg could not burn the subtitles" }
} finally {
    Pop-Location
}

Move-Item -LiteralPath $temporaryVideo -Destination $videoFile -Force
Write-Output $videoFile
