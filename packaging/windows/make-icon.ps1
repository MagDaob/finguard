param([Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$fgBitmap = New-Object System.Drawing.Bitmap 256,256
$fgGraphics = [System.Drawing.Graphics]::FromImage($fgBitmap)
$fgGraphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$fgGraphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$fgGraphics.Clear([System.Drawing.Color]::Transparent)
$fgBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml('#176e63'))
$fgPath = New-Object System.Drawing.Drawing2D.GraphicsPath
$fgPath.AddArc(12,12,64,64,180,90)
$fgPath.AddArc(180,12,64,64,270,90)
$fgPath.AddLine(244,44,244,170)
$fgPath.AddBezier(244,170,240,220,180,244,128,252)
$fgPath.AddBezier(128,252,76,244,16,220,12,170)
$fgPath.CloseFigure()
$fgGraphics.FillPath($fgBrush,$fgPath)
$fgFont = New-Object System.Drawing.Font 'Segoe UI',142,([System.Drawing.FontStyle]::Bold),([System.Drawing.GraphicsUnit]::Pixel)
$fgFormat = New-Object System.Drawing.StringFormat
$fgFormat.Alignment = [System.Drawing.StringAlignment]::Center
$fgFormat.LineAlignment = [System.Drawing.StringAlignment]::Center
$fgGraphics.DrawString('F',$fgFont,[System.Drawing.Brushes]::White,(New-Object System.Drawing.RectangleF 0,0,256,238),$fgFormat)
$fgStream = New-Object System.IO.MemoryStream
$fgBitmap.Save($fgStream,[System.Drawing.Imaging.ImageFormat]::Png)
$fgBytes = $fgStream.ToArray()
$fgFile = [System.IO.File]::Create($Output)
$fgWriter = New-Object System.IO.BinaryWriter $fgFile
try {
    $fgWriter.Write([uint16]0); $fgWriter.Write([uint16]1); $fgWriter.Write([uint16]1)
    $fgWriter.Write([byte]0); $fgWriter.Write([byte]0); $fgWriter.Write([byte]0); $fgWriter.Write([byte]0)
    $fgWriter.Write([uint16]1); $fgWriter.Write([uint16]32)
    $fgWriter.Write([uint32]$fgBytes.Length); $fgWriter.Write([uint32]22)
    $fgWriter.Write($fgBytes)
} finally {
    $fgWriter.Dispose(); $fgFile.Dispose(); $fgStream.Dispose(); $fgGraphics.Dispose()
    $fgBitmap.Dispose(); $fgBrush.Dispose(); $fgPath.Dispose(); $fgFont.Dispose(); $fgFormat.Dispose()
}
