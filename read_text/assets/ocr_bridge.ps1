$ErrorActionPreference = 'Stop'

# read_text OCR bridge.
# Speaks one line protocol over stdin/stdout:
#   in : <id><TAB><absolute png path>   |   <absolute png path>   |   QUIT
#   out: ready                 (once, at startup)
#        ok<TAB><id>           (word rows, optional "english" and more word rows, then "end")
#        err<TAB><id><TAB><message>
# Keep this file pure ASCII: Windows PowerShell 5.1 reads .ps1 without BOM
# as ANSI, and the whole point of the bridge is to stay dependency free.

$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8

Add-Type -AssemblyName System.Runtime.WindowsRuntime

$TAB = [char]9

# Wait() on a WinRT task throws AggregateException('One or more errors occurred'),
# which tells the caller nothing. Dig down to the exception that actually failed
# and keep its HRESULT, so python can show it to the user.
function Get-RootCause($err) {
    $e = $err
    while ($true) {
        if ($e -is [System.AggregateException] -and $e.InnerExceptions.Count -ge 1) {
            $e = $e.InnerExceptions[0]
            continue
        }
        if ($e.InnerException -and ($e -is [System.Reflection.TargetInvocationException] -or
                                    $e.GetType().Name -eq 'MethodInvocationException')) {
            $e = $e.InnerException
            continue
        }
        break
    }
    $msg = $e.Message
    if ($null -eq $msg) { $msg = $e.GetType().FullName }
    # Append the HRESULT only when it adds information: COM/WinRT errors normally
    # already spell it out, and 0x80131501 / 0x80004005 are just "generic error".
    $hr = $e.HResult
    if ($hr -ne 0 -and $hr -ne -2146233087 -and $hr -ne -2147467259 -and
        $msg -notmatch '0x[0-9A-Fa-f]{8}') {
        $msg = '{0} (0x{1:X8})' -f $msg, $hr
    }
    return $msg
}

function Await($WinRtTask, $ResultType) {
    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    try {
        $netTask.Wait(-1) | Out-Null
    }
    catch {
        throw (Get-RootCause $_.Exception)
    }
    $netTask.Result
}

function Write-Line($text) {
    [Console]::Out.WriteLine($text)
    [Console]::Out.Flush()
}

function Clean($text) {
    return ($text -replace "`t", ' ' -replace "`r", ' ' -replace "`n", ' ')
}

function Write-Words($result) {
    $index = 0
    foreach ($ln in $result.Lines) {
        foreach ($w in $ln.Words) {
            $rect = $w.BoundingRect
            $text = Clean $w.Text
            if (-not $text) { continue }
            $row = @($index, [int]$rect.X, [int]$rect.Y, [int]$rect.Width, [int]$rect.Height, $text) -join $TAB
            [Console]::Out.WriteLine($row)
        }
        $index++
    }
}

$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]

$languages = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages
$engine = $null
foreach ($lang in $languages) {
    if ($lang.LanguageTag -like 'zh-Hans*') {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
        break
    }
}
if (-not $engine) {
    foreach ($lang in $languages) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
        break
    }
}
if (-not $engine) {
    Write-Line ((@('err', '', 'no ocr language pack installed') -join $TAB))
    exit 1
}

$englishEngine = $null
if ($engine.RecognizerLanguage.LanguageTag -notlike 'en-*') {
    foreach ($lang in $languages) {
        if ($lang.LanguageTag -notlike 'en-*') { continue }
        try {
            $englishEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
        }
        catch {
            [Console]::Error.WriteLine('English OCR unavailable: ' + (Get-RootCause $_.Exception))
        }
        if ($englishEngine) { break }
    }
}

Write-Line 'ready'

while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    $line = $line.Trim()
    if ($line -eq 'QUIT') { break }
    if (-not $line) { continue }

    # optional request id, so python can tell a stale reply from the current one
    $id = ''
    $path = $line
    $parts = $line.Split([char[]]@([char]9), 2)
    if ($parts.Count -eq 2) {
        $id = $parts[0]
        $path = $parts[1]
    }

    try {
        if (-not [System.IO.File]::Exists($path)) {
            throw ('temp image not found: ' + $path)
        }

        # Read the bytes ourselves instead of StorageFile.GetFileFromPathAsync:
        # the WinRT file API is picky about path form / ACLs / sharing and only
        # reports it as 'One or more errors occurred'.
        $stream = [System.IO.File]::Open(
            $path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::ReadWrite)
        try {
            if ($stream.Length -le 0) {
                throw ('temp image is empty: ' + $path)
            }
            $ras = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($stream)

            $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
            # Ask for the one pixel format OCR accepts; alpha is irrelevant here.
            $bitmap = Await ($decoder.GetSoftwareBitmapAsync(
                [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
                [Windows.Graphics.Imaging.BitmapAlphaMode]::Ignore)) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        }
        finally {
            $stream.Dispose()
        }

        Write-Line ((@('ok', $id) -join $TAB))
        Write-Words $result
        if ($englishEngine -and $result.Text -cmatch '[A-Za-z0-9]') {
            Write-Line 'english'
            try {
                Write-Words (Await ($englishEngine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult]))
            }
            catch {
                [Console]::Error.WriteLine('English OCR failed: ' + (Get-RootCause $_.Exception))
            }
        }
        Write-Line 'end'
    }
    catch {
        $msg = Clean (Get-RootCause $_.Exception)
        Write-Line ((@('err', $id, $msg) -join $TAB))
    }
}
