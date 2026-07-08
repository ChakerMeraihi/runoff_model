# efm_convert_xls.ps1 -- make a CLEAN, SEPARATE copy of all EFM workbooks with old .xls
# converted to .xlsx, so the pure-stdlib Python collector can read them. The ORIGINAL
# tree is never touched: everything is written under -OutDir, MIRRORING the source folder
# structure (so the 06-EFM / month folders are preserved and efm_collect.py finds them).
#
# WHY: .xlsx is a zip of XML (stdlib reads it); .xls is the old OLE2/BIFF binary, which has
# no pure-stdlib reader. Excel is on the bank PC, so we let Excel do the one-time convert.
# .xls -> converted to .xlsx in the mirror; .xlsx/.xlsm -> copied as-is into the mirror.
#
# USAGE (bank PC):
#   powershell -ExecutionPolicy Bypass -File panel\efm_convert_xls.ps1 `
#       -Root "<...\Controle_de_gestion>" -OutDir "<...\EFM_converted>"
# THEN (pure stdlib, reads the mirror):
#   py -3 panel\efm_collect.py collect "<...\EFM_converted>" --out panel\_out\efm --panel
param(
  [Parameter(Mandatory=$true)][string]$Root,
  [Parameter(Mandatory=$true)][string]$OutDir
)

$ErrorActionPreference = "Continue"
$xlOpenXMLWorkbook = 51                                   # SaveAs format code for .xlsx

$rootFull = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# helper: source path -> mirrored destination path under OutDir (optionally new extension)
function Get-Dest([string]$src, [string]$newExt) {
  $rel = $src.Substring($rootFull.Length).TrimStart('\')
  if ($newExt) { $rel = [System.IO.Path]::ChangeExtension($rel, $newExt) }
  $dst = Join-Path $OutDir $rel
  New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
  return $dst
}

# only EFM workbooks that live under a 06-EFM folder (recurses ALL month folders)
$items = Get-ChildItem -LiteralPath $rootFull -Recurse -File -Include *.xls,*.xlsx,*.xlsm |
         Where-Object { $_.FullName -match '06-?EFM' -and $_.Name -match 'EFM' -and $_.Name -notlike '~$*' }

$excel = $null; $excelDead = $false
$conv = 0; $copied = 0; $skip = 0; $err = 0
foreach ($it in $items) {
  $src = $it.FullName
  if ($it.Extension -ieq ".xls") {
    $dst = Get-Dest $src ".xlsx"
    if (Test-Path -LiteralPath $dst) { $skip++; continue }
    if (-not $excel -and -not $excelDead) {
      try { $excel = New-Object -ComObject Excel.Application; $excel.Visible=$false; $excel.DisplayAlerts=$false }
      catch { $excelDead = $true; Write-Host "Excel COM unavailable -- skipping .xls conversions (still copying .xlsx)." }
    }
    if ($excelDead) { $err++; continue }                  # keep going; .xlsx still get copied
    try {
      $wb = $excel.Workbooks.Open($src, 0, $true)         # ReadOnly: original never modified
      $wb.SaveAs($dst, $xlOpenXMLWorkbook)
      $wb.Close($false)
      $conv++; Write-Host "CONV $($it.Name) -> $([System.IO.Path]::GetFileName($dst))"
    } catch { $err++; Write-Host "ERR  $src :: $($_.Exception.Message)" }
  } else {                                                 # already .xlsx/.xlsm -> just copy
    $dst = Get-Dest $src $null
    if (Test-Path -LiteralPath $dst) { $skip++; continue }
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $copied++; Write-Host "COPY $($it.Name)"
  }
}

if ($excel) {
  $excel.Quit(); [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
  [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}
Write-Host ""
Write-Host "done: $conv converted (.xls->.xlsx), $copied copied (.xlsx), $skip skipped, $err errors"
Write-Host "mirror written under: $OutDir   (originals untouched)"
Write-Host "next: py -3 panel\efm_collect.py collect `"$OutDir`" --out panel\_out\efm --panel"
