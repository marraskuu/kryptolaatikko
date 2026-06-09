$port = 3000
$root = $PSScriptRoot

Write-Host "Krypto Simulaattori: http://localhost:$port" -ForegroundColor Cyan
Write-Host "Pysayta: Ctrl+C"
Write-Host ""

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://localhost:$port/")
$listener.Start()

function Send-Bytes($context, [byte[]]$bytes, [string]$contentType, [int]$statusCode) {
  $context.Response.StatusCode = $statusCode
  $context.Response.ContentType = $contentType
  $context.Response.Headers.Add("Access-Control-Allow-Origin", "*")
  $context.Response.ContentLength64 = $bytes.Length
  $context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
}

function Proxy-Bitfinex($context, [string]$path) {
  $apiPath = $path.Substring("/api/bitfinex".Length)
  $query = $context.Request.Url.Query
  $apiUrl = "https://api-pub.bitfinex.com$apiPath$query"

  try {
    $response = Invoke-WebRequest -Uri $apiUrl -UseBasicParsing -TimeoutSec 60
    $bytes = [Text.Encoding]::UTF8.GetBytes($response.Content)
    Send-Bytes $context $bytes "application/json; charset=utf-8" 200
  } catch {
    $errMsg = $_.Exception.Message
    Write-Host "API proxy error: $errMsg" -ForegroundColor Red
    $body = [Text.Encoding]::UTF8.GetBytes("{ `"error`: `"$errMsg`" }")
    Send-Bytes $context $body "application/json; charset=utf-8" 502
  }
}

try {
  while ($listener.IsListening) {
    $context = $listener.GetContext()
    $path = $context.Request.Url.LocalPath

    if ($path -match '^/api/bitfinex') {
      Proxy-Bitfinex $context $path
      $context.Response.Close()
      continue
    }

    if ($path -eq "/") { $path = "/index.html" }

    $relative = $path.TrimStart("/") -replace "/", [IO.Path]::DirectorySeparatorChar
    $file = Join-Path $root $relative

    if (Test-Path $file -PathType Leaf) {
      $ext = [IO.Path]::GetExtension($file).ToLower()
      $mime = switch ($ext) {
        ".html" { "text/html; charset=utf-8" }
        ".css"  { "text/css; charset=utf-8" }
        ".js"   { "application/javascript; charset=utf-8" }
        default { "application/octet-stream" }
      }
      $bytes = [IO.File]::ReadAllBytes($file)
      Send-Bytes $context $bytes $mime 200
    } else {
      $msg = [Text.Encoding]::UTF8.GetBytes('404 Not Found')
      Send-Bytes $context $msg "text/plain; charset=utf-8" 404
    }
    $context.Response.Close()
  }
} finally {
  $listener.Stop()
}
