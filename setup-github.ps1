# Siirtaa crypto-trader-sim GitHub-repoon
# Kaytto: .\setup-github.ps1 -Username KAYTTAJANIMI [-RepoName crypto-trader-sim] [-Public]

param(
  [Parameter(Mandatory = $true)]
  [string]$Username,
  [string]$RepoName = "crypto-trader-sim",
  [switch]$Public
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-Git {
  $candidates = @(
    "$env:ProgramFiles\Git\cmd\git.exe",
    "$env:ProgramFiles\Git\bin\git.exe",
    "$env:LocalAppData\Programs\Git\cmd\git.exe"
  )
  foreach ($p in $candidates) {
    if (Test-Path $p) { return $p }
  }
  $cmd = Get-Command git -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
}

$git = Find-Git
if (-not $git) {
  Write-Host "Git ei ole asennettu." -ForegroundColor Red
  Write-Host "Asenna: winget install Git.Git"
  Write-Host "tai lataa: https://git-scm.com/download/win"
  exit 1
}

Write-Host "Kaytetaan: $git" -ForegroundColor Cyan

if (-not (Test-Path .git)) {
  & $git init
  & $git branch -M main
}

& $git add .
$status = & $git status --porcelain
if ($status) {
  & $git commit -m "Krypto-simulaattori: Bitfinex, AI-botti, veroraportti Excel"
} else {
  Write-Host "Ei uusia muutoksia commitattavaksi." -ForegroundColor Yellow
}

$remoteUrl = "https://github.com/$Username/$RepoName.git"
$existing = & $git remote get-url origin 2>$null
if ($LASTEXITCODE -ne 0) {
  & $git remote add origin $remoteUrl
} else {
  & $git remote set-url origin $remoteUrl
}

Write-Host ""
Write-Host "Remote: $remoteUrl" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Luo repo GitHubissa (jos ei ole):" -ForegroundColor Yellow
Write-Host "   https://github.com/new  -> nimi: $RepoName"
Write-Host ""
Write-Host "2. Pushaa komennolla:" -ForegroundColor Yellow
Write-Host "   git push -u origin main"
Write-Host ""
Write-Host "   (GitHub kysyy kirjautumisen ensimmaisella kerralla)" -ForegroundColor Gray

$push = Read-Host "Pushataanko nyt? (k/e)"
if ($push -eq "k") {
  & $git push -u origin main
  if ($LASTEXITCODE -eq 0) {
    Write-Host "Valmis! https://github.com/$Username/$RepoName" -ForegroundColor Green
  }
}
