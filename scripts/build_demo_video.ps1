param(
    [string]$OutputPath = "artifacts/agentauth-five-minute-demo.mp4"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$outputFile = Join-Path $projectRoot $OutputPath
$artifactDirectory = Split-Path -Parent $outputFile
$narrationPath = Join-Path $projectRoot "docs/DEMO_NARRATION.txt"
$audioPath = Join-Path $artifactDirectory "agentauth-demo-narration.mp3"
$ffmpegModule = Join-Path $projectRoot "node_modules/ffmpeg-static/ffmpeg.exe"
$ffmpeg = if (Test-Path -LiteralPath $ffmpegModule) { $ffmpegModule } else { $null }
if (-not $ffmpeg) {
    throw "Full FFmpeg is missing. Run: npm install"
}
$uv = Get-Command "uv" -ErrorAction SilentlyContinue
if (-not $uv) {
    throw "uv is required to run the free edge-tts narration generator. Install uv and retry."
}

New-Item -ItemType Directory -Path $artifactDirectory -Force | Out-Null
Push-Location $projectRoot
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "The production frontend build failed" }

    $serverLog = Join-Path $artifactDirectory "demo-server.log"
    $serverErrorLog = Join-Path $artifactDirectory "demo-server-error.log"
    $server = Start-Process -FilePath "cmd.exe" `
        -ArgumentList @("/c", "npm run start -- --hostname 127.0.0.1 --port 3000") `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $serverLog `
        -RedirectStandardError $serverErrorLog `
        -WindowStyle Hidden `
        -PassThru

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt += 1) {
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:3000" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        throw "The production frontend did not become ready. See $serverErrorLog"
    }

    $env:E2E_RECORD_DEMO = "1"
    $env:E2E_RECORD_VIDEO = "1"
    $env:PLAYWRIGHT_BASE_URL = "http://localhost:3000"
    npx playwright test e2e/demo-tour.spec.ts
    if ($LASTEXITCODE -ne 0) { throw "The recorded live demo did not pass" }
} finally {
    if ($server -and -not $server.HasExited) {
        & taskkill.exe /PID $server.Id /T /F | Out-Null
    }
    Pop-Location
}

$screenVideo = Get-ChildItem (Join-Path $projectRoot "test-results") -Recurse -Filter video.webm |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $screenVideo) { throw "Playwright did not produce a video" }

& $uv.Source run --with edge-tts edge-tts `
    --voice "en-IN-NeerjaNeural" `
    --rate "+0%" `
    --file $narrationPath `
    --write-media $audioPath
if ($LASTEXITCODE -ne 0) { throw "The free narration generator failed" }

& $ffmpeg -y -i $screenVideo -i $audioPath -map 0:v:0 -map 1:a:0 `
    -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p `
    -filter:a "loudnorm=I=-16:TP=-1.5:LRA=11,apad" `
    -c:a aac -b:a 160k -ar 48000 -movflags +faststart -t 300 $outputFile
if ($LASTEXITCODE -ne 0) { throw "FFmpeg could not assemble the narrated demo" }

Write-Output $outputFile
