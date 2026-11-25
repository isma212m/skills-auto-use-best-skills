# Installateur idempotent (Windows) : copie le skill, fusionne les hooks, ajoute le snippet CLAUDE.md.
# usage : .\install.ps1 [-Uninstall]
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dest = Join-Path $HOME '.claude\skills\skills-auto-use-best-skills'

function Invoke-Py([string]$cmd, [string[]]$pyArgs) {
  $parts = $cmd -split ' '
  $rest = @()
  if ($parts.Length -gt 1) { $rest = $parts[1..($parts.Length - 1)] }
  & $parts[0] @rest @pyArgs
}

$Py = $null
foreach ($c in @('python', 'python3', 'py -3')) {
  try {
    Invoke-Py $c @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)') 2>$null
    if ($LASTEXITCODE -eq 0) { $Py = $c; break }
  } catch { }
}
if (-not $Py) { Write-Error 'Python 3 introuvable'; exit 1 }

$merge = Join-Path $Repo 'scripts\install_merge.py'
if ($Uninstall) {
  Invoke-Py $Py @($merge, $Repo, $Py, '--uninstall')
  if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
  Write-Host "skill supprimé : $Dest"
  exit 0
}

New-Item -ItemType Directory -Force -Path (Join-Path $Dest 'scripts') | Out-Null
Copy-Item (Join-Path $Repo 'SKILL.md') (Join-Path $Dest 'SKILL.md') -Force
Copy-Item (Join-Path $Repo 'scripts\skill_router.py') (Join-Path $Dest 'scripts\skill_router.py') -Force
Invoke-Py $Py @($merge, $Repo, $Py)
Invoke-Py $Py @((Join-Path $Dest 'scripts\skill_router.py'), 'sync')
Write-Host 'OK. Relance Claude Code (ou ouvre une nouvelle session) puis tape /skills-auto-use-best-skills'
