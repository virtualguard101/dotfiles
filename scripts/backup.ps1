#Requires -Version 5.1
<#
dotfiles 备份脚本（Windows）：基于 chezmoi 备份 %USERPROFILE% 下的配置，敏感文件用 age 加密。

  status                       只读：加密配置、chezmoi 漂移、备份候选、软件包清单、权限
  backup [-n] [-Commit]        re-add 已托管文件 → 添加候选 → 更新 scoopfile.json / winget.json → check
  check                        只运行安全检查（明文密钥 / .age 格式 / 必须加密的目标 / 私钥位置），
                               规则与 pre-commit hook 共用 scripts/hooks/dotfiles_guard.py
  audit [-Apply]               敏感文件 ACL 检查，-Apply 时只保留当前用户、SYSTEM、Administrators

PowerShell 5.1 与 7 通用：pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\backup.ps1 status
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)][string]$Command = '',
  [Alias('n')][switch]$DryRun,
  [switch]$Commit,
  [switch]$Apply
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- 可调整的目标清单

# 推荐纳入 chezmoi 的配置（相对 %USERPROFILE%，用 /；目录会展开为其中的普通文件）
$HomeTrack = @(
  '.gitconfig'
  '.wslconfig'
  '.config/scoop/config.json'
  'Documents/PowerShell/Microsoft.PowerShell_profile.ps1'
  'Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1'
  'AppData/Local/Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json'
  'AppData/Roaming/Code/User/settings.json'
  'AppData/Roaming/Code/User/keybindings.json'
  'Documents/PowerToys/Backup'
)

# 目录展开时忽略的文件
$HomeExclude = @('*.lock', '*.log', '*.bak', '*~', '*_history')

# 一律加密的配置（含 token / 凭据）
$HomeSecret = @(
  '.wakatime.cfg'
  '.docker/config.json'
  '.claude/settings.json'
  'AppData/Roaming/GitHub CLI/hosts.yml'
  'AppData/Roaming/rclone/rclone.conf'
  '.npmrc'
  '.pypirc'
  '_netrc'
  '.git-credentials'
)

# 必须写在 .chezmoiignore 中的源文件（否则会被部署到 ~）
$MustIgnore = @('scripts/', 'scoopfile.json', 'winget.json', 'winget-unavailable.txt')

# ---------------------------------------------------------------- 通用工具

function Section([string]$Text) { Write-Host ''; Write-Host "== $Text ==" -ForegroundColor Blue }
function Info([string]$Text)    { Write-Host $Text }
function Ok([string]$Text)      { Write-Host '✓ ' -ForegroundColor Green -NoNewline; Write-Host $Text }
function Warn([string]$Text)    { Write-Host '! ' -ForegroundColor Yellow -NoNewline; Write-Host $Text }
function Err([string]$Text)     { Write-Host '✗ ' -ForegroundColor Red -NoNewline; Write-Host $Text }
function Die([string]$Text)     { Err $Text; exit 1 }
function Have([string]$Name)    { [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

# 调用外部程序。PowerShell 5.1 在 ErrorActionPreference=Stop 时会把重定向的 stderr 当成异常，
# 所以这里临时放宽；退出码存入 $script:Rc。
$script:Rc = 0
function Exec {
  param([string]$Exe, [string[]]$Arguments = @(), [switch]$Quiet)
  $eap = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    if ($Quiet) { & $Exe @Arguments 2>$null } else { & $Exe @Arguments }
    $script:Rc = $LASTEXITCODE
  } finally { $ErrorActionPreference = $eap }
}

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-Utf8([string]$Path, [string]$Text) {
  [IO.File]::WriteAllText($Path, ($Text -replace "`r`n", "`n"), $Utf8NoBom)
}

$HomeDir = $env:USERPROFILE
function Rel-Home([string]$Path) {
  if ($Path.StartsWith($HomeDir + '\', [StringComparison]::OrdinalIgnoreCase)) {
    return $Path.Substring($HomeDir.Length + 1).Replace('\', '/')
  }
  return $Path
}
function Norm([string]$Path) { [IO.Path]::GetFullPath($Path.Replace('/', '\')).ToLowerInvariant() }
function Is-Reparse($Item) { [bool]($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) }

# 含绝对家目录路径（C:\Users\<名>、C:/Users/<名>、JSON 中的 C:\\Users\\<名>、/c/Users/<名>）
$HomeLeaf = [regex]::Escape((Split-Path -Leaf $HomeDir))
$HomePathRx = "(?i)(?:[a-z]:|/[a-z])(?:\\\\|\\|/)users(?:\\\\|\\|/)$HomeLeaf(?![\w.-])"
function Has-HomePath([string]$File) {
  try { return [IO.File]::ReadAllText($File) -match $HomePathRx } catch { return $false }
}

$TmpPaths = New-Object System.Collections.Generic.List[string]
function Cleanup { foreach ($p in $TmpPaths) { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue } }

# ---------------------------------------------------------------- 环境与 chezmoi

$Guard = Join-Path $PSScriptRoot 'hooks\dotfiles_guard.py'
if (-not (Test-Path -LiteralPath $Guard)) { Die "缺少 $Guard" }
$Python = @('python', 'py') | Where-Object { Have $_ } | Select-Object -First 1
if (-not $Python) { Die '需要 python（3.9 及以上）' }

# guard scan：0 = 干净，1 = 命中，其他 = 出错。出错按命中处理（宁可多加密，不可漏加密）
function Has-Secret([string]$File) {
  Exec $Python @('-I', $Guard, 'scan', '-q', '--', $File) -Quiet | Out-Null
  return $script:Rc -ne 0
}

if (-not (Have chezmoi)) { Die '未找到 chezmoi' }
$Src = (Exec chezmoi @('source-path')).Trim().Replace('/', '\')
$CzConfig = (Exec chezmoi @('dump-config', '--format', 'json')) -join "`n" | ConvertFrom-Json
$CzFlags = @()

function Cz([string[]]$Arguments, [switch]$Quiet) { Exec chezmoi ($CzFlags + $Arguments) -Quiet:$Quiet }

function Age-Identities {
  @($CzConfig.age.identity) + @($CzConfig.age.identities) | Where-Object { $_ } | ForEach-Object { $_.Replace('/', '\') }
}
function Age-Recipients {
  @($CzConfig.age.recipient) + @($CzConfig.age.recipients) | Where-Object { $_ }
}

$Managed = @{}
function Load-Managed {
  $script:Managed = @{}
  foreach ($p in (Exec chezmoi @('managed', '-p', 'absolute', '-i', 'files,symlinks', '-x', 'externals'))) {
    if ($p) { $script:Managed[(Norm $p)] = $true }
  }
}

function Preflight-Crypto {
  if ($CzConfig.encryption -ne 'age') { Die 'chezmoi 未启用 age 加密（encryption = "age"）' }
  if (-not (Age-Recipients)) { Die 'chezmoi 配置缺少 age.recipient' }
  $ids = @(Age-Identities)
  if (-not $ids) { Die 'chezmoi 配置缺少 age.identity' }
  foreach ($id in $ids) {
    if (-not (Test-Path -LiteralPath $id -PathType Leaf)) { Die "age 身份文件不存在: $id" }
    if ((Norm $id).StartsWith((Norm $Src) + '\')) { Die "age 私钥位于源码目录内，会被提交: $id" }
  }
  $ignore = Join-Path $Src '.chezmoiignore'
  $lines = if (Test-Path -LiteralPath $ignore) { @(Get-Content -LiteralPath $ignore -Encoding UTF8 | ForEach-Object { $_.Trim() }) } else { @() }
  foreach ($m in $MustIgnore) {
    if ($lines -notcontains $m) { Die ".chezmoiignore 中缺少 $m，否则它会被部署到 ~" }
  }
  $docs = [Environment]::GetFolderPath('MyDocuments')
  if ((Norm $docs) -ne (Norm (Join-Path $HomeDir 'Documents'))) {
    Warn "文档文件夹被重定向到 $docs，chezmoi 中 Documents/ 下的目标与实际位置不一致"
  }
}

# 文件或目录 → 其中的普通文件（跳过重解析点、大于 1MiB 的文件与 $HomeExclude）
function Expand-Files([string]$Rel) {
  $p = Join-Path $HomeDir $Rel.Replace('/', '\')
  if (-not (Test-Path -LiteralPath $p)) { return }
  $item = Get-Item -LiteralPath $p -Force
  if (Is-Reparse $item) { return }
  if (-not $item.PSIsContainer) { return $item.FullName }
  Get-ChildItem -LiteralPath $p -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object {
      $name = $_.Name
      -not (Is-Reparse $_) -and $_.Length -lt 1MB -and -not ($HomeExclude | Where-Object { $name -like $_ })
    } |
    Sort-Object FullName | ForEach-Object FullName
}

# ---------------------------------------------------------------- 软件包清单

# 归一化导出结果，使清单只在软件包增减时变化：
#   scoop  去掉 Updated、Manifests 等每次都变的字段；Version 只在 <auto-generated> 源时 import 才会用到
#   winget 去掉 CreationDate
# 用 Python 序列化，避免 PowerShell 5.1 与 7 的 ConvertTo-Json 格式不同导致无意义的 diff。
$NormalizePy = @'
import json, sys
kind, src, dst = sys.argv[1:4]
with open(src, encoding="utf-8-sig") as f:
    d = json.load(f)
key = lambda s: s.lower()
if kind == "scoop":
    apps = []
    for a in sorted(d.get("apps") or [], key=lambda a: key(a["Name"])):
        e = {"Name": a["Name"], "Source": a["Source"], "Info": a.get("Info") or ""}
        if a["Source"] == "<auto-generated>":
            e["Version"] = a.get("Version")
        apps.append(e)
    out = {"buckets": [{"Name": b["Name"], "Source": b["Source"]}
                       for b in sorted(d.get("buckets") or [], key=lambda b: key(b["Name"]))],
           "apps": apps}
else:
    out = {"$schema": d.get("$schema"),
           "Sources": [{"Packages": sorted(({"PackageIdentifier": p["PackageIdentifier"]} for p in s.get("Packages") or []),
                                           key=lambda p: key(p["PackageIdentifier"])),
                        "SourceDetails": s["SourceDetails"]} for s in d.get("Sources") or []]}
with open(dst, "w", encoding="utf-8", newline="\n") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
    f.write("\n")
'@

function New-TempFile([string]$Name) {
  $p = [IO.Path]::Combine([IO.Path]::GetTempPath(), "dotfiles-backup-$PID-$Name")
  $TmpPaths.Add($p)
  return $p
}

function Normalize-Export([string]$Kind, [string]$RawFile) {
  $py = New-TempFile 'normalize.py'
  $out = New-TempFile "$Kind.json"
  Write-Utf8 $py $NormalizePy
  Exec $Python @('-I', $py, $Kind, $RawFile, $out) | Out-Host
  if ($script:Rc -ne 0) { throw "归一化 $Kind 导出结果失败" }
  return [IO.File]::ReadAllText($out)
}

function Export-Scoop {
  # 优先在当前进程内运行 scoop.ps1，避免经 scoop.cmd 时按控制台代码页解码输出
  $scoop = Get-Command scoop.ps1 -ErrorAction SilentlyContinue
  $exe = if ($scoop) { $scoop.Source } else { 'scoop' }
  $text = (Exec $exe @('export')) | Out-String
  if ($script:Rc -ne 0 -or -not $text.Trim()) { throw "scoop export 失败（退出码 $script:Rc）" }
  $raw = New-TempFile 'scoop-raw.json'
  Write-Utf8 $raw $text
  Normalize-Export 'scoop' $raw
}

# 无法导出的条目（不在任何 winget 源中）通过 $Unavailable 返回
function Export-Winget([ref]$Unavailable) {
  $raw = New-TempFile 'winget-raw.json'
  # winget 重定向时输出 UTF-8；PowerShell 5.1 默认按控制台代码页解码，会把中文名弄乱
  $enc = [Console]::OutputEncoding
  try { [Console]::OutputEncoding = $Utf8NoBom } catch { }
  try {
    $out = Exec winget @('export', '-o', $raw, '--accept-source-agreements', '--disable-interactivity') -Quiet
  } finally {
    try { [Console]::OutputEncoding = $enc } catch { }
  }
  if (-not (Test-Path -LiteralPath $raw)) { throw "winget export 失败（退出码 $script:Rc）" }
  $Unavailable.Value = @($out | ForEach-Object { if ("$_" -match 'not available from any source:\s*(.+?)\s*$') { $Matches[1] } } | Sort-Object -Unique)
  Normalize-Export 'winget' $raw
}

function Names-Of([string]$Json, [string]$Kind) {
  if (-not $Json) { return @() }
  $o = $Json | ConvertFrom-Json
  if ($Kind -eq 'scoop') { return @($o.apps | ForEach-Object Name) }
  return @($o.Sources | ForEach-Object { $_.Packages } | ForEach-Object PackageIdentifier)
}

function Update-PkgFile([string]$Name, [string]$Kind, [string]$New) {
  $path = Join-Path $Src $Name
  $old = if (Test-Path -LiteralPath $path) { [IO.File]::ReadAllText($path) -replace "`r`n", "`n" } else { '' }
  if ($old -eq $New) { Ok "$Name 无变化"; return }
  $before = @(Names-Of $old $Kind)
  $after = @(Names-Of $New $Kind)
  $diff = @($before | Where-Object { $after -notcontains $_ } | ForEach-Object { "  -$_" }) +
          @($after | Where-Object { $before -notcontains $_ } | ForEach-Object { "  +$_" })
  if (-not $old) { $diff = @("  （新建，$($after.Count) 项）") }
  if ($diff) { Info "${Name}:"; $diff | ForEach-Object { Info $_ } } else { Info "${Name}: 软件包不变，元数据有更新" }
  if (-not $DryRun) { Write-Utf8 $path $New }
}

function Backup-PkgList {
  Section '软件包清单'
  if (Have scoop) {
    try { Update-PkgFile 'scoopfile.json' 'scoop' (Export-Scoop) } catch { Warn "scoop export 失败: $_" }
  }
  if (Have winget) {
    try {
      $unavail = @()
      $json = Export-Winget ([ref]$unavail)
      Update-PkgFile 'winget.json' 'winget' $json
      $txt = "# winget export 无法导出的已安装程序（不在任何 winget 源中），需手动安装`n" + (($unavail | ForEach-Object { "$_`n" }) -join '')
      $path = Join-Path $Src 'winget-unavailable.txt'
      $old = if (Test-Path -LiteralPath $path) { [IO.File]::ReadAllText($path) } else { '' }
      if ($old -ne $txt) {
        Info "winget-unavailable.txt: $($unavail.Count) 项"
        if (-not $DryRun) { Write-Utf8 $path $txt }
      }
    } catch { Warn "winget export 失败: $_" }
  }
}

# ---------------------------------------------------------------- status

function Cmd-Status {
  Section '加密配置'
  Info "源目录:   $Src"
  Info "加密方式: $(if ($CzConfig.encryption) { $CzConfig.encryption } else { '未设置' })"
  Info "接收者:   $((Age-Recipients) -join ', ')"
  foreach ($id in Age-Identities) {
    if (Test-Path -LiteralPath $id -PathType Leaf) { Info "身份文件: $id（存在）" } else { Warn "身份文件不存在: $id" }
  }

  Section 'chezmoi 漂移（chezmoi status）'
  Exec chezmoi @('status') | Out-Host

  Section '备份候选（未托管）'
  Load-Managed
  $entries = @($HomeSecret | ForEach-Object { , @('secret', $_) }) + @($HomeTrack | ForEach-Object { , @('track', $_) })
  $any = $false
  foreach ($e in $entries) {
    foreach ($f in Expand-Files $e[1]) {
      if ($Managed.ContainsKey((Norm $f))) { continue }
      $flag = if ($e[0] -eq 'secret' -or (Has-Secret $f)) { 'encrypt' } else { 'plain' }
      if (Has-HomePath $f) { $flag += ',含绝对 home 路径→跳过' }
      Info ('  {0,-60} {1}' -f (Rel-Home $f), $flag)
      $any = $true
    }
  }
  if (-not $any) { Ok '无' }

  Section '软件包清单'
  foreach ($n in 'scoopfile.json', 'winget.json', 'winget-unavailable.txt') {
    $p = Join-Path $Src $n
    if (Test-Path -LiteralPath $p) { Info ('  {0,-24} 更新于 {1:yyyy-MM-dd HH:mm}' -f $n, (Get-Item -LiteralPath $p).LastWriteTime) }
    else { Warn "$n 尚未生成（backup 时生成）" }
  }

  Section '权限审计'
  Audit-Acl
  Info ''
  Info '下一步: backup -n 预览备份；audit -Apply 收紧权限'
}

# ---------------------------------------------------------------- backup

function Backup-HomeManaged {
  Section '已托管文件 re-add'
  # 明文托管的文件若出现了密钥，先转为加密，再 re-add
  foreach ($f in (Exec chezmoi @('managed', '-p', 'absolute', '-i', 'files', '-x', 'externals,encrypted,templates'))) {
    $f = $f.Replace('/', '\')
    if (-not (Test-Path -LiteralPath $f -PathType Leaf) -or -not (Has-Secret $f)) { continue }
    Warn "明文托管文件出现密钥特征，转为加密: $(Rel-Home $f)"
    Cz @('add', '--encrypt', '--', $f) | Out-Host
  }
  Cz @('re-add') | Out-Host
  if ($script:Rc -ne 0) { Die 'chezmoi re-add 失败' }
  Ok 're-add 完成'
}

function Add-HomeFile([string]$File, [string]$Kind) {
  if ($Managed.ContainsKey((Norm $File))) { return }
  $rel = Rel-Home $File
  if (Has-HomePath $File) {
    Warn "跳过（含绝对 home 路径，建议改为模板 {{ .chezmoi.homeDir }}）: $rel"
    return
  }
  if ($Kind -eq 'secret' -or (Has-Secret $File)) {
    Info "  加密  $rel"
    Cz @('add', '--encrypt', '--', $File) | Out-Host
    return
  }
  Cz @('add', '--secrets', 'error', '--', $File) -Quiet | Out-Host
  if ($script:Rc -ne 0) {
    Warn "chezmoi 密钥扫描命中，改为加密: $rel"
    Cz @('add', '--encrypt', '--', $File) | Out-Host
  } else {
    Info "  明文  $rel"
  }
}

function Backup-HomeCandidates {
  Section '添加候选配置'
  Load-Managed
  foreach ($rel in $HomeSecret) { foreach ($f in Expand-Files $rel) { Add-HomeFile $f 'secret' } }
  foreach ($rel in $HomeTrack) { foreach ($f in Expand-Files $rel) { Add-HomeFile $f 'auto' } }
}

# 安全检查：任何一项失败都返回 $false。内容检查由 dotfiles_guard.py tree 完成，与 pre-commit hook 规则一致
function Gate {
  Section '安全检查'
  $bad = $false
  foreach ($id in Age-Identities) {
    Exec git @('-C', $Src, 'ls-files', '--error-unmatch', '--', $id) -Quiet | Out-Null
    if ($script:Rc -eq 0) { Err "age 私钥被 git 跟踪: $id"; $bad = $true }
  }
  Push-Location -LiteralPath $Src
  try { Exec $Python @('-I', $Guard, 'tree') | Out-Host } finally { Pop-Location }
  if ($script:Rc -ne 0) { $bad = $true }
  if ($bad) { Err '安全检查未通过，不会提交'; return $false }
  Ok '未发现明文密钥，所有 .age 文件格式正确，敏感目标均已加密'
  return $true
}

function Cmd-Check {
  Preflight-Crypto
  if (-not (Gate)) { exit 1 }
}

function Cmd-Backup {
  if ($DryRun) { $script:CzFlags = @('--dry-run', '--verbose') }
  Preflight-Crypto
  Backup-HomeManaged
  Backup-HomeCandidates
  Backup-PkgList
  $passed = Gate
  Section '仓库状态'
  Exec git @('-C', $Src, 'status', '--short') | Out-Host
  if (-not $passed) { exit 1 }
  if ($Commit -and -not $DryRun) {
    Exec git @('-C', $Src, 'add', '-A') | Out-Host
    Exec git @('-C', $Src, 'diff', '--cached', '--quiet')
    if ($script:Rc -eq 0) { Ok '无变更可提交'; return }
    Exec git @('-C', $Src, 'commit', '-q', '-m', "backup: sync windows dotfiles ($(Get-Date -Format yyyy-MM-dd))") | Out-Host
    if ($script:Rc -ne 0) { Die 'git commit 失败（pre-commit 检查未通过？）' }
    Ok "已提交 $((Exec git @('-C', $Src, 'log', '-1', '--format=%h')))（未推送，确认后 git push）"
  }
  Info ''
  Info '提醒：age 私钥不在仓库中，请另行离线备份（密码管理器 / U 盘）：'
  Age-Identities | ForEach-Object { Info "  $_" }
}

# ---------------------------------------------------------------- audit

# 允许访问敏感文件的主体：当前用户、SYSTEM、Administrators
$AllowedSids = @(
  [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  'S-1-5-18'
  'S-1-5-32-544'
)

function Sid-Of($Identity) {
  try { return $Identity.Translate([Security.Principal.SecurityIdentifier]).Value } catch { return "$Identity" }
}

function Check-Acl([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $item = Get-Item -LiteralPath $Path -Force
  if (Is-Reparse $item) { return }
  $extra = @((Get-Acl -LiteralPath $Path).Access |
      Where-Object { $_.AccessControlType -eq 'Allow' -and $AllowedSids -notcontains (Sid-Of $_.IdentityReference) } |
      ForEach-Object { "$($_.IdentityReference)" } | Sort-Object -Unique)
  if (-not $extra) { return }
  Info ('  {0,-50} 额外可访问: {1}' -f ('~/' + (Rel-Home $Path)), ($extra -join ', '))
  $script:AuditHits++
  if (-not $Apply) { return }
  $inh = if ($item.PSIsContainer) { '(OI)(CI)' } else { '' }
  $grants = $AllowedSids | ForEach-Object { '/grant:r'; "*${_}:${inh}F" }
  $removes = $extra | ForEach-Object { '/remove:g'; $_ }
  Exec icacls (@($Path, '/inheritance:r') + $grants + $removes + @('/q')) | Out-Null
  if ($script:Rc -ne 0) { Warn "icacls 失败: $Path" }
}

function Audit-Acl {
  $script:AuditHits = 0
  foreach ($rel in $HomeSecret + @('.ssh', '.gnupg')) { Check-Acl (Join-Path $HomeDir $rel) }
  $ssh = Join-Path $HomeDir '.ssh'
  if (Test-Path -LiteralPath $ssh) {
    Get-ChildItem -LiteralPath $ssh -File -Force |
      Where-Object { $_.Extension -ne '.pub' -and $_.Name -notlike 'known_hosts*' } |
      ForEach-Object { Check-Acl $_.FullName }
  }
  foreach ($id in Age-Identities) { Check-Acl $id; Check-Acl (Split-Path -Parent $id) }
  if ($script:AuditHits -eq 0) { Ok '敏感文件权限正常' }
  elseif (-not $Apply) { Info "共 $script:AuditHits 项权限过宽，audit -Apply 收紧" }
  else { Ok "已收紧 $script:AuditHits 项权限" }
}

function Cmd-Audit {
  Section '权限审计'
  Audit-Acl
}

# ---------------------------------------------------------------- main

function Usage {
  $lines = Get-Content -LiteralPath $PSCommandPath -Encoding UTF8
  $start = [array]::IndexOf($lines, '<#') + 1
  $end = [array]::IndexOf($lines, '#>') - 1
  $lines[$start..$end] | ForEach-Object { Write-Host $_ }
}

try {
  switch ($Command) {
    'status' { Cmd-Status }
    'backup' { Cmd-Backup }
    'check'  { Cmd-Check }
    'audit'  { Cmd-Audit }
    { $_ -in '', 'help', '-h', '--help' } { Usage }
    default  { Usage; exit 2 }
  }
} finally {
  Cleanup
}
