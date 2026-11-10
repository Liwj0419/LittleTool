# 下载并安装 Python（由 assets/python_missing.hta 调用）
#
# 做四件事：找出当前最新稳定版 → 从镜像/官方依次下载 → 校验 Authenticode 签名
# → 以用户级静默方式运行安装程序。
#
# 状态写进 -StateFile（纯 ASCII 的 key=value，每次整份覆盖），HTA 轮询它来更新界面。
# 这里刻意不输出中文：状态机交给 HTA，脚本只负责干活和报告阶段，避免两边编码打架。

param(
    [string]$StateFile = (Join-Path $env:TEMP 'plug_python_install.state'),
    [string]$FallbackVersion = '3.14.10',
    [int]$PreferMinor = 14,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$CurrentVersion = ''
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Write-State {
    param(
        [string]$Stage,
        [string]$Message = '',
        [long]$Done = 0,
        [long]$Total = 0
    )
    $lines = @(
        "stage=$Stage",
        "message=$Message",
        "version=$CurrentVersion",
        "done=$Done",
        "total=$Total"
    )
    try { Set-Content -LiteralPath $StateFile -Value $lines -Encoding ASCII } catch {}
}

function Test-Installer {
    # python.org 的 ftp 里也有 3.16 这类还没有 Windows 安装包的开发版目录，
    # 光看目录名会挑到 404，必须确认 amd64.exe 真的存在。
    param([string]$Version, [string]$Arch)
    $url = "https://registry.npmmirror.com/-/binary/python/$Version/python-$Version-$Arch.exe"
    try {
        $request = [Net.HttpWebRequest]::Create($url)
        $request.Method = 'HEAD'
        $request.UserAgent = 'PlugToolbox'
        $request.Timeout = 8000
        $response = $request.GetResponse()
        $length = [long]$response.ContentLength
        $response.Close()
        return ($length -gt 10MB)
    } catch {
        return $false
    }
}

function Get-InstallerVersion {
    param([string]$Arch, [int]$PreferMinor)

    $versions = @()
    try {
        $items = Invoke-RestMethod -Uri 'https://registry.npmmirror.com/-/binary/python/' -TimeoutSec 20
        foreach ($item in $items) {
            if ($item.type -ne 'dir') { continue }
            $name = "$($item.name)".TrimEnd('/')
            if ($name -match '^3\.(\d+)\.\d+$') {
                if ([int]$Matches[1] -ge 10) { $versions += $name }
            }
        }
    } catch {}
    if ($versions.Count -eq 0) { return '' }

    $sorted = @($versions | Sort-Object { [version]$_ } -Descending)
    $pools = @()
    # 优先成熟主线：刚发布的大版本常常还缺第三方 wheel，装它对这套工具没好处。
    $preferred = @($sorted | Where-Object { ([version]$_).Minor -eq $PreferMinor })
    if ($preferred.Count -gt 0) { $pools += , $preferred }
    $pools += , $sorted

    $tried = 0
    foreach ($pool in $pools) {
        foreach ($candidate in $pool) {
            if ($tried -ge 6) { break }
            $tried++
            if (Test-Installer -Version $candidate -Arch $Arch) { return $candidate }
        }
        if ($tried -ge 6) { break }
    }
    return ''
}

function Get-RemoteFile {
    param([string]$Url, [string]$Out)
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.UserAgent = 'PlugToolbox'
    $request.Timeout = 20000
    $request.ReadWriteTimeout = 60000
    $response = $request.GetResponse()
    $total = [long]$response.ContentLength
    $stream = $response.GetResponseStream()
    $output = [IO.File]::Create($Out)
    try {
        $buffer = New-Object byte[] 131072
        $done = [long]0
        $mark = [long]0
        while (($n = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $output.Write($buffer, 0, $n)
            $done += $n
            if ($done - $mark -ge 2097152) {
                $mark = $done
                Write-State 'download' '' $done $total
            }
        }
        Write-State 'download' '' $done $total
    } finally {
        $output.Close()
        $stream.Close()
        $response.Close()
    }
}

$arch = 'amd64'
if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { $arch = 'arm64' }

Write-State 'resolve' ''
$version = Get-InstallerVersion -Arch $arch -PreferMinor $PreferMinor
if (-not $version) { $version = $FallbackVersion }
$CurrentVersion = $version

$file = "python-$version-$arch.exe"
$target = Join-Path $env:TEMP $file
$sources = @(
    "https://registry.npmmirror.com/-/binary/python/$version/$file",
    "https://mirrors.huaweicloud.com/python/$version/$file",
    "https://www.python.org/ftp/python/$version/$file"
)

$downloaded = $false
$lastError = ''
Write-State 'download' '' 0 0
foreach ($url in $sources) {
    try {
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
        Get-RemoteFile -Url $url -Out $target
        if ((Get-Item -LiteralPath $target).Length -gt 10MB) {
            $downloaded = $true
            break
        }
    } catch {
        $lastError = "$url -> $($_.Exception.Message)"
        if ($DryRun) { Write-Host "[dryrun] $lastError" }
    }
}

if (-not $downloaded) {
    Write-State 'failed' 'download_failed'
    exit 2
}

# 下到的东西必须真的是 Python 官方签的，否则不运行。
Write-State 'verify' ''
$signature = $null
try { $signature = Get-AuthenticodeSignature -FilePath $target } catch {}
$signer = ''
if ($signature -and $signature.SignerCertificate) { $signer = $signature.SignerCertificate.Subject }
if (-not $signature -or $signature.Status -ne 'Valid' -or $signer -notmatch 'Python Software Foundation') {
    Write-State 'failed' 'bad_signature'
    exit 3
}

if ($DryRun) {
    Write-State 'done' "dryrun:$version"
    exit 0
}

Write-State 'install' ''
try {
    $proc = Start-Process -FilePath $target -ArgumentList '/passive', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1' -Wait -PassThru
} catch {
    Write-State 'failed' 'install_failed'
    exit 4
}

if ($proc.ExitCode -ne 0) {
    Write-State 'failed' "install_failed:$($proc.ExitCode)"
    exit 4
}

Write-State 'done' "installed:$version"
exit 0
