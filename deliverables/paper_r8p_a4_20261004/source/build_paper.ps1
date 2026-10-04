param([string]$XeLaTeX = "xelatex", [string]$OutputDirectory = "build")
$ErrorActionPreference = "Stop"
$paperRoot = $PSScriptRoot
if (-not (Get-Command $XeLaTeX -ErrorAction SilentlyContinue)) {
    throw "XeLaTeX was not found. Set -XeLaTeX to your installed xelatex executable."
}
$paperOutput = if ([IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory } else { Join-Path $paperRoot $OutputDirectory }
New-Item -ItemType Directory -Force -Path $paperOutput | Out-Null
Push-Location -LiteralPath $paperRoot
try {
    foreach ($pass in 1..2) {
        & $XeLaTeX -interaction=nonstopmode -halt-on-error -file-line-error "-output-directory=$paperOutput" main.tex
        if ($LASTEXITCODE -ne 0) { throw "XeLaTeX pass $pass failed. See the build log." }
    }
} finally { Pop-Location }
Write-Output (Join-Path $paperOutput "main.pdf")
