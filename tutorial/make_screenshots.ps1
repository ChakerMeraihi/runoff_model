# make_screenshots.ps1 -- generate annotated PNG screenshots of every sheet of the
# run-off report (report.xlsx) for the tutorial, using Excel COM.
#
# For "headline" sheets (see annotations.json) it draws native Excel arrows + rounded
# caption boxes anchored to exact cells / chart points, then exports the region as PNG.
# Every other sheet gets a plain PNG (auto-framed on its charts, or its top-left table).
#
# The report is opened READ-ONLY and closed WITHOUT saving -> the .xlsx is never modified.
# French caption text lives in annotations.json (UTF-8) so this script can stay ASCII.
#
#   powershell -File tutorial\make_screenshots.ps1                 # all sheets
#   powershell -File tutorial\make_screenshots.ps1 -Only Synthese  # one/several (comma-sep)
#
param(
    [string]$Report  = "",
    [string]$OutDir  = "",
    [string]$Spec    = "",
    [string]$Only    = "",
    [int]   $Zoom    = 0,
    [string]$Format  = "picture"   # 'picture' (EMF, crisp) or 'bitmap'
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Report -eq "") { $Report = Join-Path $here "..\src\_out\book\report.xlsx" }
if ($OutDir -eq "") { $OutDir = Join-Path $here "screenshots" }
if ($Spec   -eq "") { $Spec   = Join-Path $here "annotations.json" }
$Report = (Resolve-Path $Report).Path
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

# spec (UTF-8)
$json = [System.IO.File]::ReadAllText((Resolve-Path $Spec).Path, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
if ($Zoom -le 0) { $Zoom = [int]$json.zoom }
if ($Zoom -le 0) { $Zoom = 140 }

# constants
$xlScreen = 1; $xlBitmap = 2; $xlPicture = -4147
$msoRoundRect = 5; $msoLineNone = 0
$C_FILL   = 255 + 249*256 + 219*65536   # amber
$C_STROKE = 214 + 116*256 +  16*65536   # orange
$C_TEXT   =  38 +  38*256 +  38*65536   # near-black

$onlySet = @()
if ($Only -ne "") { $onlySet = $Only.Split(",") | ForEach-Object { $_.Trim() } }

function Add-Callout($ws, $note) {
    # resolve anchor point (x,y in points)
    $anchor = [string]$note.anchor
    if ($anchor.StartsWith("chart")) {
        $m = [regex]::Match($anchor, '^chart(\d+)@([0-9.]+),([0-9.]+)$')
        $ci = [int]$m.Groups[1].Value; $fx = [double]$m.Groups[2].Value; $fy = [double]$m.Groups[3].Value
        $co = $ws.ChartObjects().Item($ci)
        $ax = $co.Left + $fx * $co.Width
        $ay = $co.Top  + $fy * $co.Height
    } else {
        $rng = $ws.Range($anchor)
        $ax = $rng.Left + $rng.Width  / 2.0
        $ay = $rng.Top  + $rng.Height / 2.0
    }
    # caption box
    $crng = $ws.Range([string]$note.callout)
    $cx = $crng.Left; $cy = $crng.Top
    $w = [double]$note.w; $h = [double]$note.h
    $sx = $cx + $w/2.0; $sy = $cy + $h/2.0

    # arrow first (so the box sits on top and hides its tail)
    $ln = $ws.Shapes.AddLine($sx, $sy, $ax, $ay)
    $ln.Line.ForeColor.RGB = $C_STROKE
    $ln.Line.Weight = 1.75
    try { $ln.Line.EndArrowheadStyle  = 2 } catch {}
    try { $ln.Line.EndArrowheadLength = 3 } catch {}
    try { $ln.Line.EndArrowheadWidth  = 3 } catch {}

    # rounded caption box
    $box = $ws.Shapes.AddShape($msoRoundRect, $cx, $cy, $w, $h)
    $box.Fill.ForeColor.RGB = $C_FILL
    $box.Line.ForeColor.RGB = $C_STROKE
    $box.Line.Weight = 1.25
    try { $box.Shadow.Visible = -1 } catch {}
    $tf = $box.TextFrame
    $tf.Characters().Text = [string]$note.text
    $tf.Characters().Font.Size = 9
    $tf.Characters().Font.Name = "Calibri"
    $tf.Characters().Font.Color = $C_TEXT
    try { $tf.MarginLeft = 4; $tf.MarginRight = 4; $tf.MarginTop = 2; $tf.MarginBottom = 2 } catch {}
    try { $box.TextFrame2.WordWrap = -1; $box.TextFrame2.VerticalAnchor = 1 } catch {}
}

function Export-Png($ws, $rng, $path) {
    $win = $xl.ActiveWindow
    try { $win.Zoom = $Zoom } catch {}
    $fmt = if ($Format -eq "bitmap") { $xlBitmap } else { $xlPicture }
    $scale = $Zoom / 100.0
    $done = $false
    for ($try = 0; $try -lt 4 -and -not $done; $try++) {
        try {
            $rng.CopyPicture($xlScreen, $fmt) | Out-Null
            Start-Sleep -Milliseconds 350
            $cw = [double]$rng.Width  * $scale
            $ch = [double]$rng.Height * $scale
            $co = $ws.ChartObjects().Add(2, 2, $cw, $ch)
            $chart = $co.Chart
            try { $chart.ChartArea.Format.Line.Visible = $false } catch {}
            try { $co.Border.LineStyle = $msoLineNone } catch {}
            $co.Activate() | Out-Null
            Start-Sleep -Milliseconds 200
            $chart.Paste()
            Start-Sleep -Milliseconds 200
            if ($chart.Shapes.Count -ge 1) {
                if (Test-Path $path) { Remove-Item $path -Force }
                $chart.Export($path, "PNG") | Out-Null
                $done = $true
            }
            $co.Delete()
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }
    try { $win.Zoom = 100 } catch {}
    return $done
}

Write-Output "Report : $Report"
Write-Output "OutDir : $OutDir"
Write-Output "Zoom   : $Zoom   Format: $Format"

$xl = New-Object -ComObject Excel.Application
$xl.Visible = $true          # xlScreen CopyPicture is far more reliable when visible
$xl.DisplayAlerts = $false
$xl.ScreenUpdating = $true
$wb = $xl.Workbooks.Open($Report, $false, $true)   # read-only

$order = @($json.order)
$existing = @{}
foreach ($ws in $wb.Worksheets) { $existing[$ws.Name] = $true }
# any sheets not listed in order -> append at end
foreach ($k in $existing.Keys) { if ($order -notcontains $k) { $order += $k } }

$idx = 0
try {
    foreach ($name in $order) {
        if (-not $existing.ContainsKey($name)) { continue }
        if ($onlySet.Count -gt 0 -and ($onlySet -notcontains $name)) { continue }
        $idx++
        $safe = ($name -replace '[^A-Za-z0-9_]', '_')
        $file = Join-Path $OutDir ("{0:D2}_{1}.png" -f $idx, $safe)
        try {
            $ws = $wb.Worksheets.Item($name)
            $ws.Activate()
            $head = $json.headline.$name
            if ($head -ne $null) {
                $desc = [string]$head.capture
                $rng = $ws.Range($desc)
                foreach ($note in $head.notes) { Add-Callout $ws $note }
                $tag = "annotated"
            } else {
                # inline (NOT a function): returning a COM Range from a PS function
                # makes PowerShell enumerate it into an array of cells.
                $ncharts = $ws.ChartObjects().Count
                if ($ncharts -ge 1) {
                    $maxC = [Math]::Min($ncharts, 2)
                    $minCol = 1000000; $maxCol = 0; $maxRow = 0
                    for ($i = 1; $i -le $maxC; $i++) {
                        $co = $ws.ChartObjects().Item($i)
                        if ($co.TopLeftCell.Column -lt $minCol) { $minCol = $co.TopLeftCell.Column }
                        if ($co.BottomRightCell.Column -gt $maxCol) { $maxCol = $co.BottomRightCell.Column }
                        if ($co.BottomRightCell.Row -gt $maxRow) { $maxRow = $co.BottomRightCell.Row }
                    }
                    $rng = $ws.Range($ws.Cells.Item(1, $minCol), $ws.Cells.Item($maxRow + 1, $maxCol + 1))
                } else {
                    $ur = $ws.UsedRange
                    $lastRow = [Math]::Min($ur.Row + $ur.Rows.Count - 1, 45)
                    $lastCol = [Math]::Min($ur.Column + $ur.Columns.Count - 1, 24)
                    $rng = $ws.Range($ws.Cells.Item(1, 1), $ws.Cells.Item($lastRow, $lastCol))
                }
                $desc = "auto"
                try { $desc = [string]$rng.Address($false, $false) } catch {}
                $tag = "plain"
            }
            $ok = Export-Png $ws $rng $file
            $stat = "FAILED"; if ($ok) { $stat = "OK" }
        } catch {
            $tag = "ERROR"; $desc = ""; $stat = $_.Exception.Message
        }
        Write-Output ("[{0,2}] {1,-22} {2,-9} {3,-10} -> {4}" -f $idx, $name, $tag, $desc, $stat)
    }
} finally {
    $wb.Close($false)
    $xl.Quit()
}
Write-Output "DONE ($idx sheets)"
