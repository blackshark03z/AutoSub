param(
  [ValidateSet("Prepare", "Ready")]
  [string]$Mode = "Prepare"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (
  (Split-Path -Leaf $scriptDir) -eq "overnight_inputs" -and
  (Split-Path -Leaf (Split-Path -Parent $scriptDir)) -eq "templates"
) {
  $root = Split-Path -Parent (Split-Path -Parent $scriptDir)
}
else {
  $root = $scriptDir
}

function Test-PlaceholderValue {
  param([string]$Value)
  if ([string]::IsNullOrWhiteSpace($Value)) { return $true }
  return $Value -match 'PASTE_|NOT_YET|CHANGEME|YOUR_|<'
}

function Get-KeyFileSummary {
  param([string]$Path)

  $summary = [ordered]@{
    Exists = $false
    Invalid = $false
    RawNonCommentLines = 0
    UniqueConfiguredKeyCount = 0
    PlaceholderCount = 0
    DuplicateCount = 0
    Status = "MISSING"
  }

  if (-not (Test-Path $Path)) {
    return $summary
  }

  $summary.Exists = $true
  try {
    $nonCommentLines = @(
      Get-Content $Path | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_) -and -not $_.TrimStart().StartsWith("#")
      } | ForEach-Object { $_.Trim() }
    )

    $summary.RawNonCommentLines = $nonCommentLines.Count
    $configuredKeys = @($nonCommentLines | Where-Object { -not (Test-PlaceholderValue $_) })
    $summary.PlaceholderCount = @($nonCommentLines | Where-Object { Test-PlaceholderValue $_ }).Count
    $summary.UniqueConfiguredKeyCount = @($configuredKeys | Select-Object -Unique).Count

    $duplicateTotal = 0
    foreach ($group in ($nonCommentLines | Group-Object)) {
      if ($group.Count -gt 1) {
        $duplicateTotal += ($group.Count - 1)
      }
    }
    $summary.DuplicateCount = $duplicateTotal

    if ($summary.RawNonCommentLines -eq 0) {
      $summary.Status = "PLACEHOLDER"
    }
    elseif ($summary.UniqueConfiguredKeyCount -gt 0 -and $summary.PlaceholderCount -eq 0) {
      $summary.Status = "CONFIGURED"
    }
    elseif ($summary.PlaceholderCount -gt 0 -or $summary.UniqueConfiguredKeyCount -eq 0) {
      $summary.Status = "PLACEHOLDER"
    }
  }
  catch {
    $summary.Invalid = $true
    $summary.Status = "INVALID_FORMAT"
  }

  return $summary
}

function Get-GitIgnoredState {
  param([string]$RelativePath)
  $git = Get-Command git -ErrorAction SilentlyContinue
  if (-not $git) {
    return @{ Available = $false; Ignored = $false }
  }

  $result = & git -C $root check-ignore $RelativePath 2>$null
  return @{ Available = $true; Ignored = ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace(($result | Out-String))) }
}

function Get-GitTrackableState {
  param([string]$RelativePath)
  $git = Get-Command git -ErrorAction SilentlyContinue
  if (-not $git) {
    return @{ Available = $false; Trackable = $false }
  }

  & git -C $root check-ignore $RelativePath 2>$null | Out-Null
  return @{ Available = $true; Trackable = ($LASTEXITCODE -ne 0) }
}

$errors = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]

$requiredDirectories = @(
  "input",
  "secrets",
  "operator",
  "evidence",
  "templates",
  "templates\\overnight_inputs",
  "templates\\legacy",
  "templates\\legacy\\PREP00",
  "checkpoints"
)
foreach ($dir in $requiredDirectories) {
  if (-not (Test-Path (Join-Path $root $dir))) {
    $errors.Add("Missing directory: $dir")
  }
}

$activeTemplates = @(
  "templates\\overnight_inputs\\elevenlabs_api.example.txt",
  "templates\\overnight_inputs\\gemini_api.example.txt",
  "templates\\overnight_inputs\\translation_config.env.example",
  "templates\\overnight_inputs\\run_config.example.json",
  "templates\\overnight_inputs\\source_provenance.example.json",
  "templates\\overnight_inputs\\PREPARE_OVERNIGHT_INPUTS.md",
  "templates\\overnight_inputs\\validate_overnight_inputs.ps1"
)
foreach ($template in $activeTemplates) {
  if (-not (Test-Path (Join-Path $root $template))) {
    $errors.Add("Missing active template: $template")
  }
}

$conflictingActiveFormats = @(
  "secrets\\elevenlabs_keys.json",
  "secrets\\translation_provider.env",
  "templates\\overnight_inputs\\elevenlabs_keys.example.json",
  "templates\\overnight_inputs\\translation_provider.env.example"
)
foreach ($path in $conflictingActiveFormats) {
  if (Test-Path (Join-Path $root $path)) {
    $errors.Add("Conflicting old secret format still active: $path")
  }
}

$elevenlabsPath = Join-Path $root "secrets\\elevenlabs_api.txt"
$geminiPath = Join-Path $root "secrets\\gemini_api.txt"
$translationConfigPath = Join-Path $root "operator\\translation_config.env"
$runConfigPath = Join-Path $root "operator\\run_config.json"
$provenancePath = Join-Path $root "operator\\source_provenance.json"
$sourcePath = Join-Path $root "input\\source.mp4"

$elevenlabsSummary = Get-KeyFileSummary -Path $elevenlabsPath
$geminiSummary = Get-KeyFileSummary -Path $geminiPath

$runtimeStatus = [ordered]@{
  ElevenLabs = $elevenlabsSummary.Status
  Gemini = $geminiSummary.Status
  TranslationConfig = "MISSING"
  RunConfig = "MISSING"
  Provenance = "MISSING"
  SourceVideo = "MISSING"
}

$translationMap = @{}
$translationConfigInvalid = $false
if (Test-Path $translationConfigPath) {
  try {
    foreach ($line in Get-Content $translationConfigPath) {
      if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) { continue }
      $parts = $line.Split("=", 2)
      if ($parts.Count -ne 2) {
        $translationConfigInvalid = $true
        break
      }
      $translationMap[$parts[0].Trim()] = $parts[1].Trim()
    }

    $requiredEnv = @(
      "TRANSLATION_PROVIDER",
      "TRANSLATION_BASE_URL",
      "TRANSLATION_MODEL",
      "TRANSLATION_TIMEOUT_SECONDS",
      "TRANSLATION_KEY_FILE"
    )
    foreach ($name in $requiredEnv) {
      if (-not $translationMap.ContainsKey($name)) {
        $errors.Add("Missing env var in operator\\translation_config.env: $name")
      }
    }

    if ($translationMap.ContainsKey("TRANSLATION_PROVIDER") -and $translationMap["TRANSLATION_PROVIDER"] -ne "gemini_openai_compatible") {
      $warnings.Add("TRANSLATION_PROVIDER differs from gemini_openai_compatible.")
    }
    if ($translationMap.ContainsKey("TRANSLATION_BASE_URL") -and $translationMap["TRANSLATION_BASE_URL"] -ne "https://generativelanguage.googleapis.com/v1beta/openai/") {
      $warnings.Add("TRANSLATION_BASE_URL differs from the Gemini OpenAI-compatible endpoint.")
    }
    if ($translationMap.ContainsKey("TRANSLATION_KEY_FILE") -and $translationMap["TRANSLATION_KEY_FILE"] -ne "secrets\gemini_api.txt") {
      $errors.Add("operator\\translation_config.env TRANSLATION_KEY_FILE must be secrets\\gemini_api.txt")
    }

    if ($translationConfigInvalid) {
      $runtimeStatus.TranslationConfig = "INVALID_FORMAT"
    }
    elseif (
      $translationMap.ContainsKey("TRANSLATION_MODEL") -and
      -not (Test-PlaceholderValue $translationMap["TRANSLATION_MODEL"])
    ) {
      $runtimeStatus.TranslationConfig = "CONFIGURED"
    }
    else {
      $runtimeStatus.TranslationConfig = "PLACEHOLDER"
    }
  }
  catch {
    $translationConfigInvalid = $true
    $runtimeStatus.TranslationConfig = "INVALID_FORMAT"
  }
}

if ($translationConfigInvalid) {
  $errors.Add("Invalid env format: operator\\translation_config.env")
}

$runConfigJson = $null
if (Test-Path $runConfigPath) {
  try {
    $runConfigJson = Get-Content -Raw $runConfigPath | ConvertFrom-Json
    $requiredTopLevel = @(
      "source_language", "target_language", "target_locale", "market_profile_id",
      "content_mode", "audio_policy", "preview", "final", "auto_upload", "push",
      "stop_before", "source", "translation", "tts"
    )
    foreach ($field in $requiredTopLevel) {
      if (-not ($runConfigJson.PSObject.Properties.Name -contains $field)) {
        $errors.Add("run_config.json missing field: $field")
      }
    }

    if ($runConfigJson.source.path -ne "input\source.mp4") { $errors.Add("run_config.json source.path must be input\\source.mp4") }
    if ($runConfigJson.translation.provider -ne "gemini_openai_compatible") { $errors.Add("run_config.json translation.provider must be gemini_openai_compatible") }
    if ($runConfigJson.translation.config_path -ne "operator\translation_config.env") { $errors.Add("run_config.json translation.config_path must be operator\\translation_config.env") }
    if ($runConfigJson.translation.key_file -ne "secrets\gemini_api.txt") { $errors.Add("run_config.json translation.key_file must be secrets\\gemini_api.txt") }
    if ($runConfigJson.tts.key_file -ne "secrets\elevenlabs_api.txt") { $errors.Add("run_config.json tts.key_file must be secrets\\elevenlabs_api.txt") }
    if ($runConfigJson.tts.key_selection_policy -ne "sticky_failover") { $errors.Add("run_config.json tts.key_selection_policy must be sticky_failover") }
    if ($runConfigJson.tts.preferred_voice_id -ne $null -and (Test-PlaceholderValue ([string]$runConfigJson.tts.preferred_voice_id))) {
      $warnings.Add("preferred_voice_id is set but still looks like a placeholder.")
    }
    $runtimeStatus.RunConfig = "CONFIGURED"
  }
  catch {
    $runtimeStatus.RunConfig = "INVALID_FORMAT"
    $errors.Add("Invalid JSON: operator\\run_config.json")
  }
}

if (Test-Path $provenancePath) {
  try {
    $provenanceJson = Get-Content -Raw $provenancePath | ConvertFrom-Json
    $requiredProvFields = @("source_platform", "rights_status", "operator_note", "operator_acknowledged_at")
    foreach ($field in $requiredProvFields) {
      if (-not ($provenanceJson.PSObject.Properties.Name -contains $field)) {
        $errors.Add("source_provenance.json missing field: $field")
      }
    }
    $runtimeStatus.Provenance = "CONFIGURED"
  }
  catch {
    $runtimeStatus.Provenance = "INVALID_FORMAT"
    $errors.Add("Invalid JSON: operator\\source_provenance.json")
  }
}

if (Test-Path $sourcePath) {
  $runtimeStatus.SourceVideo = "CONFIGURED"
  $sourceHash = (Get-FileHash -Algorithm SHA256 $sourcePath).Hash.ToLowerInvariant()
  $sourceSize = (Get-Item $sourcePath).Length
  Write-Host "Source video detected: sha256=$sourceHash size_bytes=$sourceSize"
  if ($sourceHash -ne "34a304fb44f5e4c27d1a34989a69f939888ef90c89bbae0142434f43cf4db068") {
    $warnings.Add("Source hash differs from the canonical sample baseline.")
  }
}

$pythonAvailable = [bool](Get-Command python -ErrorAction SilentlyContinue)
if (-not $pythonAvailable) {
  $warnings.Add("python not found in PATH.")
}

$ffmpegAvailable = [bool](Get-Command ffmpeg -ErrorAction SilentlyContinue)
$ffprobeAvailable = [bool](Get-Command ffprobe -ErrorAction SilentlyContinue)
if (-not $ffmpegAvailable) { $warnings.Add("ffmpeg not found in PATH.") }
if (-not $ffprobeAvailable) { $warnings.Add("ffprobe not found in PATH.") }

$fontPath = "C:\\Windows\\Fonts\\arial.ttf"
if ($runConfigJson -and $runConfigJson.subtitle -and $runConfigJson.subtitle.font_path) {
  $fontPath = [string]$runConfigJson.subtitle.font_path
}
if (-not (Test-Path $fontPath)) { $warnings.Add("Configured font not found: $fontPath") }

$drive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($root).TrimEnd('\').TrimEnd(':')) -ErrorAction SilentlyContinue
$freeGb = $null
if ($drive) {
  $freeGb = [math]::Round($drive.Free / 1GB, 2)
  if ($freeGb -lt 20) {
    $warnings.Add("Free disk is below 20 GB: $freeGb GB")
  }
}

$ignoredChecks = @(
  @{ Path = "secrets\\elevenlabs_api.txt"; Label = "secrets\\elevenlabs_api.txt" },
  @{ Path = "secrets\\gemini_api.txt"; Label = "secrets\\gemini_api.txt" },
  @{ Path = "operator\\translation_config.env"; Label = "operator\\translation_config.env" },
  @{ Path = "operator\\run_config.json"; Label = "operator\\run_config.json" },
  @{ Path = "operator\\source_provenance.json"; Label = "operator\\source_provenance.json" },
  @{ Path = "input\\source.mp4"; Label = "input\\source.mp4" }
)
foreach ($check in $ignoredChecks) {
  $ignoredState = Get-GitIgnoredState -RelativePath $check.Path
  if ($ignoredState.Available -and -not $ignoredState.Ignored) {
    $errors.Add("Git ignore check failed for $($check.Label)")
  }
}

$trackableChecks = @(
  "templates\\overnight_inputs\\elevenlabs_api.example.txt",
  "templates\\overnight_inputs\\gemini_api.example.txt",
  "templates\\overnight_inputs\\translation_config.env.example",
  "templates\\overnight_inputs\\run_config.example.json",
  "templates\\overnight_inputs\\source_provenance.example.json"
)
foreach ($path in $trackableChecks) {
  $trackableState = Get-GitTrackableState -RelativePath $path
  if ($trackableState.Available -and -not $trackableState.Trackable) {
    $errors.Add("Trackable template is incorrectly ignored: $path")
  }
}

if ($Mode -eq "Ready") {
  if (-not $pythonAvailable) {
    $errors.Add("Ready mode requires python in PATH.")
  }
  if ($runtimeStatus.SourceVideo -ne "CONFIGURED") {
    $errors.Add("Ready mode requires input\\source.mp4.")
  }
  if ($elevenlabsSummary.UniqueConfiguredKeyCount -lt 1) {
    $errors.Add("Ready mode requires at least one configured ElevenLabs key.")
  }
  if ($geminiSummary.UniqueConfiguredKeyCount -lt 1) {
    $errors.Add("Ready mode requires at least one configured Gemini key.")
  }
  if ($runtimeStatus.TranslationConfig -ne "CONFIGURED") {
    $errors.Add("Ready mode requires a configured Gemini model in operator\\translation_config.env.")
  }
}

foreach ($warning in $warnings) {
  Write-Warning $warning
}

Write-Host "Validation mode: $Mode"
Write-Host "Runtime status: ElevenLabs=$($runtimeStatus.ElevenLabs); Gemini=$($runtimeStatus.Gemini); TranslationConfig=$($runtimeStatus.TranslationConfig); RunConfig=$($runtimeStatus.RunConfig); Provenance=$($runtimeStatus.Provenance); SourceVideo=$($runtimeStatus.SourceVideo)"
Write-Host "ElevenLabs raw non-comment lines: $($elevenlabsSummary.RawNonCommentLines)"
Write-Host "ElevenLabs unique configured key count: $($elevenlabsSummary.UniqueConfiguredKeyCount)"
Write-Host "ElevenLabs placeholder count: $($elevenlabsSummary.PlaceholderCount)"
Write-Host "ElevenLabs duplicate count: $($elevenlabsSummary.DuplicateCount)"
Write-Host "Gemini raw non-comment lines: $($geminiSummary.RawNonCommentLines)"
Write-Host "Gemini unique configured key count: $($geminiSummary.UniqueConfiguredKeyCount)"
Write-Host "Gemini placeholder count: $($geminiSummary.PlaceholderCount)"
Write-Host "Gemini duplicate count: $($geminiSummary.DuplicateCount)"
Write-Host "Translation model placeholder: $(if ($runtimeStatus.TranslationConfig -eq 'PLACEHOLDER') { 'YES' } else { 'NO' })"
Write-Host "Python available: $pythonAvailable"
Write-Host "FFmpeg available: $ffmpegAvailable"
Write-Host "ffprobe available: $ffprobeAvailable"
Write-Host "Font path: $fontPath"
if ($null -ne $freeGb) {
  Write-Host "Free disk GB: $freeGb"
}

if ($errors.Count -gt 0) {
  foreach ($errorLine in $errors) {
    Write-Host "ERROR: $errorLine"
  }
  exit 1
}

if ($Mode -eq "Prepare") {
  Write-Host "Preparation validation: PASS"
}
else {
  Write-Host "Ready validation: PASS"
}
exit 0
