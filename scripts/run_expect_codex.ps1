param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string]$Message,

  [string]$BaseUrl = "",

  [int]$TimeoutMs = 1800000,

  [switch]$Headed,

  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"

if (-not $BaseUrl) {
  if ($env:EXPECT_BASE_URL) {
    $BaseUrl = $env:EXPECT_BASE_URL
  } else {
    $BaseUrl = "https://policy.reformparty.kr"
  }
}

$env:EXPECT_BASE_URL = $BaseUrl

$argsList = @(
  "-a", "codex",
  "-m", $Message,
  "-y",
  "--no-cookies",
  "--timeout", $TimeoutMs.ToString()
)

if ($Headed) {
  $argsList += "--headed"
}

if ($ExtraArgs) {
  $argsList += $ExtraArgs
}

Write-Host "[expect] base-url: $BaseUrl"
Write-Host "[expect] agent: codex"
Write-Host "[expect] cookies: disabled"

& expect-cli @argsList
$code = $LASTEXITCODE
if ($code -ne 0) {
  exit $code
}
