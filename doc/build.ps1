# Builds the DAV run-off companion into a single PDF.
# Usage:  .\build.ps1            (full doc -> dav_runoff.pdf)
#         .\build.ps1 ch03       (single file matching *ch03* -> _preview.pdf)

param([string]$only)

$root = $PSScriptRoot

if ($only) {
    $f = Get-ChildItem -Path $root -Recurse -Filter "*$only*.md" | Select-Object -First 1
    if (-not $f) { Write-Error "No file matching *$only*.md"; exit 1 }
    pandoc $f.FullName --metadata-file="$root\meta.yaml" -N --pdf-engine=xelatex `
        -o "$root\_preview.pdf"
    Write-Host "Built _preview.pdf from $($f.Name)"
    return
}

$files = Get-Content "$root\build_order.txt" |
    Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') } |
    ForEach-Object { Join-Path $root $_.Trim() }

$missing = $files | Where-Object { -not (Test-Path $_) }
if ($missing) { $missing | ForEach-Object { Write-Warning "missing: $_" } }

pandoc ($files | Where-Object { Test-Path $_ }) `
    --metadata-file="$root\meta.yaml" `
    --toc --toc-depth=2 -N `
    --pdf-engine=xelatex `
    -o "$root\dav_runoff.pdf"

$mb = [math]::Round((Get-Item "$root\dav_runoff.pdf").Length / 1MB, 2)
Write-Host "Built dav_runoff.pdf ($mb MB)"
