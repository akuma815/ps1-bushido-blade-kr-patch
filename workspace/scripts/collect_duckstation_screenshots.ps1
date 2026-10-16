param(
    [string]$Source = 'H:\Duckstation\screenshots',
    [string]$Destination = (Join-Path $PSScriptRoot '..\..\work\screenshots\original'),
    [datetime]$Since = [datetime]::MinValue
)

$sourceRoot = (Resolve-Path -LiteralPath $Source).Path
$destinationRoot = [System.IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Path $destinationRoot -Force | Out-Null

$copied = @()
Get-ChildItem -LiteralPath $sourceRoot -File -Filter '*.png' |
    Where-Object { $_.LastWriteTime -ge $Since } |
    Sort-Object LastWriteTime, Name |
    ForEach-Object {
        $target = Join-Path $destinationRoot $_.Name
        Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        $hash = Get-FileHash -LiteralPath $target -Algorithm SHA256
        $copied += [pscustomobject]@{
            Name = $_.Name
            CapturedAt = $_.LastWriteTime.ToString('o')
            SHA256 = $hash.Hash
        }
    }

$copied | Format-Table -AutoSize
Write-Output "Copied $($copied.Count) screenshot(s) to $destinationRoot"
