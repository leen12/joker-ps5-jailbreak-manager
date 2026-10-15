param(
    [Parameter(Mandatory = $true)][string]$HostName,
    [int]$WebPort = 8080,
    [int]$ManagerPort = 8084,
    [int]$JokerPort = 9310,
    [ValidateSet('Auto', 'PayloadManager', 'Websrv')][string]$Transport = 'Auto',
    [switch]$Build
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$payload = Join-Path $PSScriptRoot 'jokerd.elf'
if ($Build) {
    & (Join-Path $PSScriptRoot 'build.ps1')
    if ($LASTEXITCODE -ne 0) { throw "build failed ($LASTEXITCODE)" }
}
if (-not (Test-Path -LiteralPath $payload)) { throw "missing payload: $payload" }
if ($JokerPort -lt 1 -or $JokerPort -gt 65535) { throw "invalid Joker port: $JokerPort" }

function Test-TcpPort([string]$TargetHost, [int]$Port, [int]$TimeoutMs = 750) {
    $tcp = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $tcp.ConnectAsync($TargetHost, $Port)
        return $task.Wait($TimeoutMs) -and $tcp.Connected
    } catch {
        return $false
    } finally {
        $tcp.Dispose()
    }
}

function Stop-JokerDaemon([string]$TargetHost, [int]$Port) {
    if (-not (Test-TcpPort $TargetHost $Port)) { return }
    $tcp = [System.Net.Sockets.TcpClient]::new($TargetHost, $Port)
    try {
        $stream = $tcp.GetStream()
        $bytes = [System.Text.Encoding]::UTF8.GetBytes("quit`n")
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush()
    } finally {
        $tcp.Dispose()
    }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 250
        if (-not (Test-TcpPort $TargetHost $Port 250)) { return }
    }
    throw "existing jokerd on ${TargetHost}:$Port did not stop"
}

function Assert-JokerHello([string]$TargetHost, [int]$Port) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 250
        $tcp = [System.Net.Sockets.TcpClient]::new()
        try {
            if (-not $tcp.ConnectAsync($TargetHost, $Port).Wait(250)) { continue }
            $stream = $tcp.GetStream()
            $stream.ReadTimeout = 1000
            $request = [System.Text.Encoding]::UTF8.GetBytes("hello`n")
            $stream.Write($request, 0, $request.Length)
            $buffer = [byte[]]::new(4096)
            $count = $stream.Read($buffer, 0, $buffer.Length)
            $reply = [System.Text.Encoding]::UTF8.GetString($buffer, 0, $count)
            if ($reply -match '"name"\s*:\s*"jokerd"') { return $reply.Trim() }
        } catch {
            # The daemon may still be binding. Retry within the bounded window.
        } finally {
            $tcp.Dispose()
        }
    }
    throw "jokerd did not answer hello on ${TargetHost}:$Port"
}

if ($Transport -eq 'Auto') {
    $Transport = if ($JokerPort -eq 9310) { 'PayloadManager' } else { 'Websrv' }
}

$bytes = [System.IO.File]::ReadAllBytes($payload)

if ($Transport -eq 'PayloadManager') {
    if ($JokerPort -ne 9310) {
        throw 'PayloadManager launches without argv; use -Transport Websrv for a custom JokerPort'
    }
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    try {
        $filename = 'jokerd-current.elf'
        $uploadUrl = "http://${HostName}:$ManagerPort/manage:upload?filename=$filename"
        $content = [System.Net.Http.ByteArrayContent]::new($bytes)
        $content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new('application/octet-stream')
        $response = $client.PostAsync($uploadUrl, $content).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            throw "Payload Manager upload returned $([int]$response.StatusCode)"
        }
        Stop-JokerDaemon $HostName $JokerPort
        $remotePath = '/data/pldmgr/payloads/jokerd-current/jokerd-current.elf'
        $launchUrl = "http://${HostName}:$ManagerPort/loadpayload:$remotePath"
        $response = $client.GetAsync($launchUrl).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            throw "Payload Manager launch returned $([int]$response.StatusCode)"
        }
        $hello = Assert-JokerHello $HostName $JokerPort
        Write-Host "launched $($bytes.Length) bytes through Payload Manager; hello=$hello"
        return
    } finally {
        $client.Dispose()
        $handler.Dispose()
    }
}

$endpoint = "http://${HostName}:$WebPort/elfldr"
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$form = [System.Net.Http.MultipartFormDataContent]::new()
try {
    $elf = [System.Net.Http.ByteArrayContent]::new($bytes)
    $elf.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new('application/octet-stream')
    $form.Add($elf, 'elf', 'jokerd.elf')
    $form.Add([System.Net.Http.StringContent]::new("jokerd.elf $JokerPort"), 'args')
    $form.Add([System.Net.Http.StringContent]::new('0'), 'pipe')
    $response = $client.PostAsync($endpoint, $form).GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) {
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        throw "websrv loader returned $([int]$response.StatusCode): $body"
    }
    $hello = Assert-JokerHello $HostName $JokerPort
    Write-Host "launched $($bytes.Length) bytes through $endpoint on Joker port $JokerPort; hello=$hello"
} finally {
    $form.Dispose()
    $client.Dispose()
    $handler.Dispose()
}
