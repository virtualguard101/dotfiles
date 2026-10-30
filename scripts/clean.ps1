#Requires -Version 5.1
<#
缓存清理脚本（Windows）：覆盖 %TEMP%、本机已安装工具的包缓存与应用缓存。

  scan [-Deep]                 只读：各目标占用 / 状态、%LOCALAPPDATA% 未覆盖项、残留目录
  list                         列出全部清理目标及其层级（不计算大小）
  run [-Apply] [-Deep] [-Exclude 目标,...] [目标|default|opt|all]...
                               默认 dry-run，只打印将执行的命令；不指定目标时等同 default
  leftovers                    只读：已卸载应用的残留目录、久未使用的 Electron 配置目录

PowerShell 5.1 与 7 通用：pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\clean.ps1 scan
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)][string]$Command = '',
  [switch]$Apply,
  [switch]$Deep,
  [Alias('x')][string[]]$Exclude = @(),
  [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Targets = @()
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- 可调参数（同名环境变量覆盖）

function Env-Int([string]$Name, [int]$Default) {
  $v = [Environment]::GetEnvironmentVariable($Name)
  if ($v -match '^\d+$') { return [int]$v }
  return $Default
}
$TempDays = Env-Int 'TEMP_DAYS' 7               # temp：清理多少天未修改的临时文件
$StaleDays = Env-Int 'STALE_DAYS' 180           # leftovers：多少天无写入视为久未使用
$PatchMinutes = 60                              # precommit：保留多少分钟内的 stash 补丁
$UncoveredMinMB = Env-Int 'UNCOVERED_MIN_MB' 100

$HomeDir = $env:USERPROFILE
$LocalApp = $env:LOCALAPPDATA
$RoamingApp = $env:APPDATA
$ScoopDir = if ($env:SCOOP) { $env:SCOOP } else { Join-Path $HomeDir 'scoop' }
$CacheHome = if ($env:XDG_CACHE_HOME) { $env:XDG_CACHE_HOME } else { Join-Path $HomeDir '.cache' }
$CargoDir = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $HomeDir '.cargo' }
$GradleDir = if ($env:GRADLE_USER_HOME) { $env:GRADLE_USER_HOME } else { Join-Path $HomeDir '.gradle' }
$PreCommitHome = if ($env:PRE_COMMIT_HOME) { $env:PRE_COMMIT_HOME } else { Join-Path $CacheHome 'pre-commit' }
$WuDownload = Join-Path $env:SystemRoot 'SoftwareDistribution\Download'

# ---------------------------------------------------------------- 目标清单
# id|层级|说明。default 层被 run 默认包含；opt 层必须显式点名（或用 opt / all）。

$TargetList = @(
  "temp|default|%TEMP% 中超过 TEMP_DAYS($TempDays) 天未修改的文件与目录（被占用的文件跳过）"
  'scoop|default|scoop cleanup -a -k：旧版本与过期安装包（-Deep 额外 scoop cache rm -a）'
  'electron|default|%APPDATA% 下 Electron 应用的 Cache、Code Cache、GPUCache 等（应用运行时跳过）'
  'browser|default|Edge / Chrome / Brave 各配置文件的 Cache、Code Cache、GPUCache 与着色器缓存（浏览器运行时跳过）'
  'firefox|default|Firefox 网络缓存 cache2 / startupCache / thumbnails（配置文件被占用时跳过）'
  'updaters|default|%LOCALAPPDATA%\*-updater 中 electron-updater 下载的安装包（应用运行时跳过）'
  'uv|default|uv cache prune（-Deep: uv cache clean）'
  'pip|default|pip cache purge'
  'pnpm|default|pnpm store prune + %LOCALAPPDATA%\pnpm-cache 元数据缓存'
  'npm|default|npm cache clean --force + _logs（-Deep 额外删除 _npx）'
  'bun|default|bun pm cache rm'
  'yarn|default|yarn 全局缓存目录'
  'go|default|go clean -cache（-Deep 额外 go clean -modcache；Go 已卸载时删除 %LOCALAPPDATA%\go-build）'
  'cargo|default|%CARGO_HOME%\registry\src 与 git\checkouts（-Deep 额外 registry\cache 与 git\db）'
  'nuget|default|dotnet nuget locals http-cache --clear'
  "precommit|default|pre-commit gc + 删除 $PatchMinutes 分钟前遗留的 stash 补丁 patch*（-Deep: pre-commit clean）"
  'crashdumps|opt|%LOCALAPPDATA%\CrashDumps 与 WER 报告（调试崩溃时可能需要）'
  'cloudmusic|opt|网易云音乐播放缓存 NetEase\CloudMusic\Cache（不含下载的歌曲；运行时跳过）'
  'shader|opt|DirectX / NVIDIA / AMD 着色器缓存（删除后首次启动游戏会卡顿）'
  'nugetpkgs|opt|dotnet nuget locals global-packages --clear（下次构建重新下载全部包）'
  'maven|opt|%USERPROFILE%\.m2\repository（下次构建重新下载全部依赖）'
  'gradle|opt|%GRADLE_USER_HOME%\caches 与 daemon（先 gradle --stop）'
  'recycle|opt|清空所有驱动器的回收站（不可恢复）'
  'docker|opt|docker builder prune + image prune（仅 dangling 镜像；需要 Docker Desktop 在运行）'
  'wu|opt|Windows Update 下载缓存 SoftwareDistribution\Download（需要管理员）'
  'dism|opt|DISM /StartComponentCleanup 清理组件存储（需要管理员，耗时较长）'
)

# 已卸载应用的残留：检测名…|路径…（均用 ; 分隔，路径相对 %USERPROFILE%）。检测名任一满足即视为已安装：
# 命令、已安装程序名（“应用和功能”中的名称）、scoop 应用，或含 / 的相对路径存在。目录正被占用时也视为在用
$LeftoverMap = @(
  'cursor|AppData/Roaming/Cursor'
  'cursor;cursor-agent;AppData/Local/cursor-agent|.cursor'
  'code;Microsoft Visual Studio Code|AppData/Roaming/Code;.vscode'
  'chrome;Google Chrome|AppData/Local/Google/Chrome'
  'notion|AppData/Roaming/Notion'
  'Feishu;飞书;Lark|AppData/Roaming/LarkShell;AppData/Roaming/LarkShellSingleton'
  'Docker Desktop|AppData/Roaming/Docker;AppData/Roaming/Docker Desktop'
  'MongoDB Compass;mongodb-compass|AppData/Roaming/MongoDB Compass'
  'Quark;夸克|AppData/Roaming/Quark;AppData/Roaming/The Quark Authors'
  'BaiduNetdisk;百度网盘|AppData/Roaming/BaiduNetdisk;AppData/Roaming/BaiduYunGuanjia;AppData/Roaming/BaiduYunKernel'
  'WeMeet;腾讯会议;TencentMeeting|AppData/Roaming/WeMeetApp'
  'draw.io|AppData/Roaming/draw.io'
  'Wireshark|AppData/Roaming/Wireshark'
  'VMware Workstation;VMware Player|AppData/Roaming/VMware'
  'Apifox|AppData/Roaming/apifox'
  'uTools|AppData/Roaming/uTools'
  'Clash Party;Mihomo Party;mihomo-party|AppData/Roaming/mihomo-party'
  'MongoDB;mongodb|AppData/Roaming/mongodb'
  'Nushell;nu|AppData/Roaming/nushell'
)

$ElectronCacheDirs = @('Cache', 'Code Cache', 'GPUCache', 'DawnGraphiteCache', 'DawnWebGPUCache', 'GPUPersistentCache', 'GrShaderCache', 'GraphiteDawnCache', 'ShaderCache')
$VscodeFamily = @('Code', 'Code - Insiders', 'VSCodium', 'Cursor', 'Windsurf', 'Kiro', 'Trae')
$VscodeCacheDirs = @('CachedData', 'CachedExtensionVSIXs', 'CachedProfilesData', 'logs')
# %APPDATA% 目录名与进程名不一致的应用（用于没有锁文件时的运行检测）
$ProcessAlias = @{ 'LarkShell' = @('Feishu', 'Lark'); 'Docker Desktop' = @('Docker Desktop'); 'Code' = @('Code') }

$Browsers = @(
  'Microsoft\Edge\User Data'
  'Google\Chrome\User Data'
  'BraveSoftware\Brave-Browser\User Data'
)
$BrowserProfileCaches = @('Cache', 'Code Cache', 'GPUCache', 'DawnGraphiteCache', 'DawnWebGPUCache')
$BrowserRootCaches = @('GrShaderCache', 'GraphiteDawnCache', 'ShaderCache', 'GPUPersistentCache')

# ---------------------------------------------------------------- 通用工具

function Section([string]$Text) { Write-Host ''; Write-Host "== $Text ==" -ForegroundColor Blue }
function Info([string]$Text)    { Write-Host $Text }
function Dim([string]$Text)     { Write-Host $Text -ForegroundColor DarkGray }
function Ok([string]$Text)      { Write-Host '✓ ' -ForegroundColor Green -NoNewline; Write-Host $Text }
function Warn([string]$Text)    { Write-Host '! ' -ForegroundColor Yellow -NoNewline; Write-Host $Text }
function Err([string]$Text)     { Write-Host '✗ ' -ForegroundColor Red -NoNewline; Write-Host $Text }
function Die([string]$Text)     { Err $Text; exit 2 }

# 只认可执行文件（.exe / .cmd），不走可能被执行策略拦截的 .ps1 shim
function Tool([string]$Name) {
  $c = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($c) { return $c.Source }
  return $null
}
function Have([string]$Name) { [bool](Tool $Name) }

function Tilde([string]$Path) {
  if ($Path.StartsWith($HomeDir, [StringComparison]::OrdinalIgnoreCase)) { return '~' + $Path.Substring($HomeDir.Length) }
  return $Path
}

function Is-Reparse($Item) { [bool]($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) }

$script:Rc = 0
# 调用外部程序；PowerShell 5.1 在 ErrorActionPreference=Stop 时会把重定向的 stderr 当成异常，所以这里临时放宽
function Exec {
  param([string]$Exe, [string[]]$Arguments = @(), [switch]$Quiet)
  $eap = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    if ($Quiet) { & $Exe @Arguments 2>$null } else { & $Exe @Arguments }
    $script:Rc = $LASTEXITCODE
  } catch {
    $script:Rc = 1
  } finally { $ErrorActionPreference = $eap }
}
# 取外部程序输出的第一行（用于查询缓存路径）；失败返回 $null
function Query([string]$Name, [string[]]$Arguments) {
  $exe = Tool $Name
  if (-not $exe) { return $null }
  $out = @(Exec $exe $Arguments -Quiet) | Where-Object { "$_".Trim() } | Select-Object -First 1
  if ($script:Rc -ne 0 -or -not $out) { return $null }
  return "$out".Trim()
}

$script:Dry = -not $Apply
$script:Failed = $false
$script:Locked = 0

function Run-Cmd([string]$Name, [string[]]$Arguments) {
  if ($script:Dry) { Dim "  + $Name $($Arguments -join ' ')"; return }
  $exe = Tool $Name
  if (-not $exe) { Warn "未找到 $Name（继续）"; $script:Failed = $true; return }
  Exec $exe $Arguments | Out-Host
  if ($script:Rc -ne 0) { Warn "命令失败（继续）: $Name $($Arguments -join ' ')"; $script:Failed = $true }
}

# 删除只允许发生在这些目录之下，且不能是这些目录本身
$SafeRoots = @($HomeDir, $ScoopDir, $WuDownload) | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') }
$Protected = @($HomeDir, $LocalApp, $RoamingApp, $env:TEMP, $ScoopDir, $CacheHome, (Join-Path $HomeDir 'Documents'),
  (Join-Path $HomeDir 'Desktop'), (Join-Path $HomeDir 'Downloads')) |
  Where-Object { $_ } | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') }

function Assert-Deletable([string]$Path) {
  if (-not $Path -or -not [IO.Path]::IsPathRooted($Path)) { throw "拒绝删除非绝对路径: '$Path'" }
  $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
  if ($Protected -contains $full -or $full.Length -le 3) { throw "拒绝删除受保护的目录: $full" }
  $inside = $SafeRoots | Where-Object { $full.StartsWith($_ + '\', [StringComparison]::OrdinalIgnoreCase) }
  if (-not $inside) { throw "拒绝删除允许范围以外的路径: $full" }
}

# 递归删除：不跟随重解析点（只删除链接本身）；被占用的文件跳过并计数
function Remove-Tree([string]$Path) {
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  if (-not $item) { return }
  if ($item.PSIsContainer -and -not (Is-Reparse $item)) {
    foreach ($child in @($item.EnumerateFileSystemInfos())) { Remove-Tree $child.FullName }
  }
  try {
    if ($item.Attributes -band [IO.FileAttributes]::ReadOnly) { $item.Attributes = $item.Attributes -bxor [IO.FileAttributes]::ReadOnly }
    if ($item.PSIsContainer) { [IO.Directory]::Delete($item.FullName, $false) } else { [IO.File]::Delete($item.FullName) }
  } catch {
    if (-not $item.PSIsContainer) { $script:Locked++ }
  }
}

function Remove-Paths([string[]]$Paths) {
  foreach ($p in $Paths) {
    if (-not $p -or -not (Test-Path -LiteralPath $p)) { continue }
    Assert-Deletable $p
    if ($script:Dry) { Dim "  + 删除 $(Tilde $p)"; continue }
    Remove-Tree $p
  }
}

# 若干路径的磁盘占用（字节）。手工递归：跳过重解析点，无权限的目录忽略
function Size-Of([string[]]$Paths) {
  [long]$total = 0
  foreach ($p in $Paths) {
    if (-not $p) { continue }
    $item = Get-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue
    if (-not $item) { continue }
    if (-not $item.PSIsContainer) { $total += $item.Length; continue }
    if (Is-Reparse $item) { continue }
    $stack = New-Object System.Collections.Generic.Stack[IO.DirectoryInfo]
    $stack.Push($item)
    while ($stack.Count) {
      $d = $stack.Pop()
      try {
        foreach ($e in $d.EnumerateFileSystemInfos()) {
          if ($e.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
          if ($e -is [IO.DirectoryInfo]) { $stack.Push($e) } else { $total += $e.Length }
        }
      } catch { }
    }
  }
  return $total
}

function Human([long]$Bytes) {
  if ($Bytes -le 0) { return '0' }
  $units = 'B', 'K', 'M', 'G', 'T'
  $v = [double]$Bytes; $i = 0
  while ($v -ge 1024 -and $i -lt $units.Count - 1) { $v /= 1024; $i++ }
  if ($i -eq 0) { return "$Bytes" + 'B' }
  return ('{0:0.#}{1}' -f $v, $units[$i])
}

# 文件是否被其他进程独占（Chromium 的 lockfile、Firefox 的 parent.lock 在程序运行时独占打开）
function Test-Locked([string]$File) {
  if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { return $false }
  try { $s = [IO.File]::Open($File, 'Open', 'ReadWrite', 'None'); $s.Close(); return $false } catch { return $true }
}

# VS Code 的 code.lock 中是 PID
function Test-PidLock([string]$File) {
  if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { return $false }
  $text = (Get-Content -LiteralPath $File -Raw -ErrorAction SilentlyContinue)
  if ("$text" -notmatch '(\d+)') { return $false }
  return [bool](Get-Process -Id ([int]$Matches[1]) -ErrorAction SilentlyContinue)
}

$script:ProcNames = $null
function Proc-Running([string[]]$Names) {
  if ($null -eq $script:ProcNames) {
    $script:ProcNames = @{}
    Get-Process | ForEach-Object { $script:ProcNames[$_.ProcessName.ToLowerInvariant()] = $true }
  }
  foreach ($n in $Names) { if ($script:ProcNames.ContainsKey($n.ToLowerInvariant())) { return $true } }
  return $false
}

function Sub-Dirs([string]$Path, [string]$Filter = '*') {
  if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return }
  Get-ChildItem -LiteralPath $Path -Directory -Force -Filter $Filter -ErrorAction SilentlyContinue |
    Where-Object { -not (Is-Reparse $_) } | ForEach-Object FullName
}

function Existing([string[]]$Paths) { $Paths | Where-Object { $_ -and (Test-Path -LiteralPath $_) } }

# Paths 输出中以 "!" 开头的是跳过说明，其余是要删除的路径
function Clean-FromPaths([string]$Id) {
  foreach ($p in @(& $Impl[$Id].Paths)) {
    if (-not $p) { continue }
    if ("$p".StartsWith('!')) { Warn "$($p.Substring(1))，跳过" } else { Remove-Paths @($p) }
  }
}

# ---------------------------------------------------------------- 目标实现
# Paths：输出本次会被删除/修剪的路径（用于计算占用）
# Clean：执行清理（dry-run 时只打印）
# Avail（可选）：返回 $false 表示目标不适用（工具未安装）
# Size（可选）：覆盖默认的占用统计（字节；返回 -1 表示无法统计）

$Impl = @{}

# --- %TEMP%：顶层条目中最近修改时间早于阈值的文件 / 目录
function Temp-Stale {
  $cutoff = (Get-Date).AddDays(-$TempDays)
  foreach ($e in @(Get-ChildItem -LiteralPath $env:TEMP -Force -ErrorAction SilentlyContinue)) {
    if ($e.LastWriteTime -gt $cutoff) { continue }
    if ($e.PSIsContainer -and -not (Is-Reparse $e)) {
      $recent = Get-ChildItem -LiteralPath $e.FullName -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -gt $cutoff } | Select-Object -First 1
      if ($recent) { continue }
    }
    $e.FullName
  }
}
$Impl['temp'] = @{
  Avail = { Test-Path -LiteralPath $env:TEMP }
  Paths = { Temp-Stale }
  Clean = {
    $items = @(Temp-Stale)
    if (-not $script:Dry) { Remove-Paths $items; return }
    $items | ForEach-Object { Assert-Deletable $_ }
    Dim "  + 删除 %TEMP% 中 $($items.Count) 个顶层条目（$(Human (Size-Of $items))）"
  }
}

# --- scoop：旧版本目录（current 之外）与非当前版本的安装包
function Scoop-Stale {
  foreach ($app in @(Sub-Dirs (Join-Path $ScoopDir 'apps'))) {
    $cur = Get-Item -LiteralPath (Join-Path $app 'current') -Force -ErrorAction SilentlyContinue
    if (-not $cur) { continue }
    $target = if (Is-Reparse $cur) { Split-Path -Leaf "$(@($cur.Target)[0])" } else { 'current' }
    foreach ($v in @(Sub-Dirs $app)) {
      $leaf = Split-Path -Leaf $v
      if ($leaf -ne 'current' -and $leaf -ne $target) { $v }
    }
  }
  $cache = Join-Path $ScoopDir 'cache'
  if (-not (Test-Path -LiteralPath $cache)) { return }
  $installed = @{}
  foreach ($app in @(Sub-Dirs (Join-Path $ScoopDir 'apps'))) {
    $m = Join-Path $app 'current\manifest.json'
    if (Test-Path -LiteralPath $m) {
      try { $installed[(Split-Path -Leaf $app)] = ([IO.File]::ReadAllText($m) | ConvertFrom-Json).version } catch { }
    }
  }
  foreach ($f in @(Get-ChildItem -LiteralPath $cache -File -Force -ErrorAction SilentlyContinue)) {
    $parts = $f.Name -split '#'
    if ($Deep -or $parts.Count -lt 2 -or $installed[$parts[0]] -ne $parts[1]) { $f.FullName }
  }
}
$Impl['scoop'] = @{
  Avail = { Have 'scoop' }
  Paths = { Scoop-Stale }
  Clean = { Run-Cmd 'scoop' @('cleanup', '-a', '-k'); if ($Deep) { Run-Cmd 'scoop' @('cache', 'rm', '-a') } }
}

# --- Electron（%APPDATA%\<App>）：只认带 Chromium 标记（Local State 或 GPUCache）的目录
function Is-VscodeFamily([string]$Name) { $VscodeFamily -contains $Name }
function Electron-AppDirs {
  foreach ($d in @(Sub-Dirs $RoamingApp)) {
    $name = Split-Path -Leaf $d
    if (Is-VscodeFamily $name) { $d; continue }
    if ((Test-Path -LiteralPath (Join-Path $d 'Local State')) -or (Test-Path -LiteralPath (Join-Path $d 'GPUCache'))) { $d }
  }
}
function Electron-Busy([string]$Dir) {
  $name = Split-Path -Leaf $Dir
  $codeLock = Join-Path $Dir 'code.lock'
  if (Test-Path -LiteralPath $codeLock) { return Test-PidLock $codeLock }
  $lock = Join-Path $Dir 'lockfile'
  if (Test-Path -LiteralPath $lock) { return Test-Locked $lock }
  $names = @($name, ($name -replace '\s', ''))
  if ($ProcessAlias.ContainsKey($name)) { $names += $ProcessAlias[$name] }
  return Proc-Running $names
}
function Electron-Paths {
  foreach ($d in @(Electron-AppDirs)) {
    $name = Split-Path -Leaf $d
    if (Electron-Busy $d) { "!$name 正在运行"; continue }
    $bases = @($d) + @(Sub-Dirs $d 'Default') + @(Sub-Dirs $d 'Profile*') + @(Sub-Dirs (Join-Path $d 'Partitions'))
    foreach ($b in $bases) { Existing ($ElectronCacheDirs | ForEach-Object { Join-Path $b $_ }) }
    if (Is-VscodeFamily $name) { Existing ($VscodeCacheDirs | ForEach-Object { Join-Path $d $_ }) }
  }
}
$Impl['electron'] = @{
  Paths = { Electron-Paths }
  Clean = { Clean-FromPaths 'electron' }
}

# --- Chromium 浏览器（%LOCALAPPDATA%\…\User Data）
function Browser-Paths {
  foreach ($rel in $Browsers) {
    $root = Join-Path $LocalApp $rel
    if (-not (Test-Path -LiteralPath $root)) { continue }
    if (Test-Locked (Join-Path $root 'lockfile')) { "!$(($rel -split '\\')[1]) 正在运行"; continue }
    $profiles = @(Sub-Dirs $root 'Default') + @(Sub-Dirs $root 'Profile *') + @(Sub-Dirs $root 'Guest Profile')
    foreach ($p in $profiles) { Existing ($BrowserProfileCaches | ForEach-Object { Join-Path $p $_ }) }
    Existing ($BrowserRootCaches | ForEach-Object { Join-Path $root $_ })
  }
}
$Impl['browser'] = @{
  Avail = { [bool]($Browsers | Where-Object { Test-Path -LiteralPath (Join-Path $LocalApp $_) }) }
  Paths = { Browser-Paths }
  Clean = { Clean-FromPaths 'browser' }
}

# --- Firefox：缓存在 Local，锁在 Roaming 的同名配置目录中
function Firefox-Paths {
  foreach ($p in @(Sub-Dirs (Join-Path $LocalApp 'Mozilla\Firefox\Profiles'))) {
    $name = Split-Path -Leaf $p
    if (Test-Locked (Join-Path $RoamingApp "Mozilla\Firefox\Profiles\$name\parent.lock")) { "!Firefox 配置 $name 正在使用"; continue }
    Existing (@('cache2', 'startupCache', 'thumbnails', 'jumpListCache') | ForEach-Object { Join-Path $p $_ })
  }
}
$Impl['firefox'] = @{
  Avail = { Test-Path -LiteralPath (Join-Path $LocalApp 'Mozilla\Firefox\Profiles') }
  Paths = { Firefox-Paths }
  Clean = { Clean-FromPaths 'firefox' }
}

# --- electron-updater 下载的安装包：%LOCALAPPDATA%\<app>-updater\{installer.exe,pending}
# 应用运行时可能正在等待退出后安装 pending 中的更新，所以跳过
function Updater-Paths {
  foreach ($d in @(Sub-Dirs $LocalApp '*-updater')) {
    $app = (Split-Path -Leaf $d) -replace '-updater$', '' -replace '^@[^-]+-', ''
    $roaming = Join-Path $RoamingApp $app
    if ((Proc-Running @($app, ($app -replace '-', ''), ($app -replace '-', ' '))) -or
        ((Test-Path -LiteralPath $roaming) -and (Electron-Busy $roaming))) { "!$app 正在运行"; continue }
    Existing @((Join-Path $d 'installer.exe'), (Join-Path $d 'pending'))
  }
}
$Impl['updaters'] = @{
  Avail = { [bool](Sub-Dirs $LocalApp '*-updater') }
  Paths = { Updater-Paths }
  Clean = { Clean-FromPaths 'updaters' }
}

# --- 网易云音乐播放缓存（不含下载的歌曲；也可以在客户端设置中限制缓存大小）
$CloudMusicCache = Join-Path $LocalApp 'NetEase\CloudMusic\Cache'
$Impl['cloudmusic'] = @{
  Avail = { Test-Path -LiteralPath $CloudMusicCache }
  Paths = { if (Proc-Running @('cloudmusic')) { '!网易云音乐正在运行'; return }; $CloudMusicCache }
  Clean = { Clean-FromPaths 'cloudmusic' }
}

# --- uv
$Impl['uv'] = @{
  Avail = { Have 'uv' }
  Paths = { $d = Query 'uv' @('cache', 'dir'); if ($d) { $d } else { Join-Path $LocalApp 'uv\cache' } }
  Clean = { if ($Deep) { Run-Cmd 'uv' @('cache', 'clean') } else { Run-Cmd 'uv' @('cache', 'prune') } }
}

# --- pip
$Impl['pip'] = @{
  Avail = { Have 'pip' }
  Paths = { $d = Query 'pip' @('cache', 'dir'); if ($d) { $d } else { Join-Path $LocalApp 'pip\Cache' } }
  Clean = { Run-Cmd 'pip' @('cache', 'purge') }
}

# --- pnpm
$Impl['pnpm'] = @{
  Avail = { Have 'pnpm' }
  Paths = { Query 'pnpm' @('store', 'path'); Join-Path $LocalApp 'pnpm-cache' }
  Clean = { Run-Cmd 'pnpm' @('store', 'prune'); Remove-Paths @(Join-Path $LocalApp 'pnpm-cache') }
}

# --- npm
function Npm-Root { $r = Query 'npm' @('config', 'get', 'cache'); if ($r) { $r } else { Join-Path $LocalApp 'npm-cache' } }
$Impl['npm'] = @{
  Avail = { Have 'npm' }
  Paths = { $r = Npm-Root; Join-Path $r '_cacache'; Join-Path $r '_logs'; if ($Deep) { Join-Path $r '_npx' } }
  Clean = {
    $r = Npm-Root
    Run-Cmd 'npm' @('cache', 'clean', '--force')
    Remove-Paths @(Join-Path $r '_logs')
    if ($Deep) { Remove-Paths @(Join-Path $r '_npx') }
  }
}

# --- bun
$Impl['bun'] = @{
  Avail = { Have 'bun' }
  Paths = {
    if ($env:BUN_INSTALL_CACHE_DIR) { $env:BUN_INSTALL_CACHE_DIR }
    else { Join-Path $(if ($env:BUN_INSTALL) { $env:BUN_INSTALL } else { Join-Path $HomeDir '.bun' }) 'install\cache' }
  }
  Clean = { Run-Cmd 'bun' @('pm', 'cache', 'rm') }
}

# --- yarn（classic 与 berry 的全局缓存都直接删目录，避免 berry 在项目外拒绝运行）
$YarnDirs = @((Join-Path $LocalApp 'Yarn\Cache'), (Join-Path $HomeDir '.yarn\berry\cache'))
$Impl['yarn'] = @{
  Avail = { [bool](Existing $YarnDirs) }
  Paths = { $YarnDirs }
  Clean = { Remove-Paths $YarnDirs }
}

# --- go（Go 已卸载但构建缓存还在时，直接删目录）
$GoBuildDir = Join-Path $LocalApp 'go-build'
$Impl['go'] = @{
  Avail = { (Have 'go') -or (Test-Path -LiteralPath $GoBuildDir) }
  Paths = {
    if (-not (Have 'go')) { $GoBuildDir; return }
    Query 'go' @('env', 'GOCACHE'); if ($Deep) { Query 'go' @('env', 'GOMODCACHE') }
  }
  Clean = {
    if (-not (Have 'go')) { Remove-Paths @($GoBuildDir); return }
    Run-Cmd 'go' @('clean', '-cache'); if ($Deep) { Run-Cmd 'go' @('clean', '-modcache') }
  }
}

# --- cargo
$Impl['cargo'] = @{
  Avail = { (Test-Path -LiteralPath (Join-Path $CargoDir 'registry')) -or (Test-Path -LiteralPath (Join-Path $CargoDir 'git')) }
  Paths = {
    if (Proc-Running @('cargo')) { '!cargo 正在运行'; return }
    Join-Path $CargoDir 'registry\src'; Join-Path $CargoDir 'git\checkouts'
    if ($Deep) { Join-Path $CargoDir 'registry\cache'; Join-Path $CargoDir 'git\db' }
  }
  Clean = { Clean-FromPaths 'cargo' }
}

# --- NuGet HTTP 缓存
function Nuget-Http { if ($env:NUGET_HTTP_CACHE_PATH) { $env:NUGET_HTTP_CACHE_PATH } else { Join-Path $LocalApp 'NuGet\v3-cache' } }
$Impl['nuget'] = @{
  Avail = { Test-Path -LiteralPath (Nuget-Http) }
  Paths = { Nuget-Http }
  Clean = { if (Have 'dotnet') { Run-Cmd 'dotnet' @('nuget', 'locals', 'http-cache', '--clear') } else { Remove-Paths @(Nuget-Http) } }
}

# --- pre-commit：stash 补丁可能含未暂存的明文；只删旧补丁，避免影响正在运行的提交
function Precommit-Patches {
  $cutoff = (Get-Date).AddMinutes(-$PatchMinutes)
  Get-ChildItem -LiteralPath $PreCommitHome -File -Filter 'patch*' -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt $cutoff } | ForEach-Object FullName
}
$Impl['precommit'] = @{
  Avail = { (Have 'pre-commit') -and (Test-Path -LiteralPath $PreCommitHome) }
  Paths = { $PreCommitHome }
  Clean = {
    if ($Deep) { Run-Cmd 'pre-commit' @('clean'); return }
    Run-Cmd 'pre-commit' @('gc')
    Remove-Paths @(Precommit-Patches)
  }
}

# --- 崩溃转储与 WER 报告
$CrashDirs = @((Join-Path $LocalApp 'CrashDumps'), (Join-Path $LocalApp 'Microsoft\Windows\WER\ReportArchive'), (Join-Path $LocalApp 'Microsoft\Windows\WER\ReportQueue'))
$Impl['crashdumps'] = @{
  Avail = { [bool](Existing $CrashDirs) }
  Paths = { Existing $CrashDirs }
  Clean = { Clean-FromPaths 'crashdumps' }
}

# --- 着色器缓存（正在运行的游戏占用的文件会被跳过）
$ShaderDirs = @('D3DSCache', 'NVIDIA\DXCache', 'NVIDIA\GLCache', 'AMD\DxCache', 'AMD\DxcCache', 'AMD\VkCache', 'Intel\ShaderCache') |
  ForEach-Object { Join-Path $LocalApp $_ }
$Impl['shader'] = @{
  Avail = { [bool](Existing $ShaderDirs) }
  Paths = { Existing $ShaderDirs }
  Clean = { Clean-FromPaths 'shader' }
}

# --- NuGet 全局包目录
$NugetPkgs = if ($env:NUGET_PACKAGES) { $env:NUGET_PACKAGES } else { Join-Path $HomeDir '.nuget\packages' }
$Impl['nugetpkgs'] = @{
  Avail = { Test-Path -LiteralPath $NugetPkgs }
  Paths = { if (Proc-Running @('devenv', 'MSBuild', 'dotnet')) { '!Visual Studio / MSBuild / dotnet 正在运行'; return }; $NugetPkgs }
  Clean = {
    if (Proc-Running @('devenv', 'MSBuild', 'dotnet')) { Warn 'Visual Studio / MSBuild / dotnet 正在运行，跳过'; return }
    if (Have 'dotnet') { Run-Cmd 'dotnet' @('nuget', 'locals', 'global-packages', '--clear') } else { Remove-Paths @($NugetPkgs) }
  }
}

# --- Maven
$Impl['maven'] = @{
  Avail = { Test-Path -LiteralPath (Join-Path $HomeDir '.m2\repository') }
  Paths = {
    $java = Get-CimInstance Win32_Process -Filter "Name='java.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'plexus\.classworlds' }
    if ($java) { '!Maven 正在运行'; return }
    Join-Path $HomeDir '.m2\repository'
  }
  Clean = { Clean-FromPaths 'maven' }
}

# --- Gradle
$Impl['gradle'] = @{
  Avail = { Test-Path -LiteralPath (Join-Path $GradleDir 'caches') }
  Paths = { Join-Path $GradleDir 'caches'; Join-Path $GradleDir 'daemon' }
  Clean = {
    if (Have 'gradle') { Run-Cmd 'gradle' @('--stop', '-q') }
    Remove-Paths @((Join-Path $GradleDir 'caches'), (Join-Path $GradleDir 'daemon'))
  }
}

# --- 回收站
function Recycle-Size {
  try {
    [long]$sum = 0
    foreach ($i in @((New-Object -ComObject Shell.Application).NameSpace(10).Items())) { $sum += [long]$i.Size }
    return $sum
  } catch { return -1 }
}
$Impl['recycle'] = @{
  Paths = { }
  Size  = { Recycle-Size }
  Clean = {
    if ($script:Dry) { Dim '  + Clear-RecycleBin -Force'; return }
    try { Clear-RecycleBin -Force -ErrorAction Stop } catch { Warn "清空回收站失败: $_"; $script:Failed = $true }
  }
}

# --- Docker（Docker Desktop 未运行时命名管道不存在）
$Impl['docker'] = @{
  Avail = { (Have 'docker') -and (Test-Path -LiteralPath '\\.\pipe\docker_engine') }
  Paths = { }
  Size  = {
    [long]$sum = 0
    foreach ($line in @(Exec (Tool 'docker') @('system', 'df', '--format', '{{.Type}}|{{.Reclaimable}}') -Quiet)) {
      if ("$line" -match '^(Build Cache|Images)\|([\d.]+)\s*([kMGT]?B)') {
        $mult = @{ 'B' = 1; 'kB' = 1e3; 'MB' = 1e6; 'GB' = 1e9; 'TB' = 1e12 }[$Matches[3]]
        $sum += [long]([double]$Matches[2] * $mult)
      }
    }
    return $sum
  }
  Clean = { Run-Cmd 'docker' @('builder', 'prune', '-f'); Run-Cmd 'docker' @('image', 'prune', '-f') }
}

# --- 系统级（需要管理员）
function Is-Admin {
  ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
$Impl['wu'] = @{
  Avail = { Test-Path -LiteralPath $WuDownload }
  Paths = { Get-ChildItem -LiteralPath $WuDownload -Force -ErrorAction SilentlyContinue | ForEach-Object FullName }
  Clean = {
    if ($script:Dry) { Dim "  + Stop-Service wuauserv, bits；删除 $WuDownload\*；Start-Service wuauserv, bits"; return }
    if (-not (Is-Admin)) { Warn '需要在管理员 PowerShell 中运行，跳过'; $script:Failed = $true; return }
    Stop-Service wuauserv, bits -Force
    try { Clean-FromPaths 'wu' } finally { Start-Service wuauserv, bits }
  }
}
$Impl['dism'] = @{
  Paths = { }
  Size  = { -1 }
  Clean = {
    if ($script:Dry) { Dim '  + DISM /Online /Cleanup-Image /StartComponentCleanup'; return }
    if (-not (Is-Admin)) { Warn '需要在管理员 PowerShell 中运行，跳过'; $script:Failed = $true; return }
    Run-Cmd 'dism' @('/Online', '/Cleanup-Image', '/StartComponentCleanup')
  }
}

# ---------------------------------------------------------------- 目标调度

$Order = @(); $Tier = @{}; $Desc = @{}
foreach ($t in $TargetList) {
  $tid, $ttier, $tdesc = $t -split '\|', 3
  $Order += $tid; $Tier[$tid] = $ttier; $Desc[$tid] = $tdesc
}

function Target-Avail([string]$Id) {
  $a = $Impl[$Id].Avail
  if (-not $a) { return $true }
  try { return [bool](& $a) } catch { return $false }
}

# 返回 @{ Bytes; Skips }
function Measure-Target([string]$Id) {
  $paths = @(); $skips = @()
  foreach ($p in @(& $Impl[$Id].Paths)) {
    if (-not $p) { continue }
    if ("$p".StartsWith('!')) { $skips += "$p".Substring(1) } else { $paths += "$p" }
  }
  $bytes = if ($Impl[$Id].Size) { [long](& $Impl[$Id].Size) } else { Size-Of $paths }
  return @{ Bytes = $bytes; Skips = $skips }
}

# ---------------------------------------------------------------- 子命令

function Cmd-List {
  Info ('{0,-11} {1,-8} {2}' -f '目标', '层级', '说明')
  foreach ($id in $Order) { Info ('{0,-11} {1,-8} {2}' -f $id, $Tier[$id], $Desc[$id]) }
}

function Scan-Uncovered {
  Section "%LOCALAPPDATA% 与 ~/.cache 中未被任何目标覆盖的项（≥ ${UncoveredMinMB}M）"
  $saved = $script:Deep; $script:Deep = $true   # 以 -Deep 的路径集合判断覆盖范围
  $covered = @{}
  foreach ($id in $Order) {
    if (-not (Target-Avail $id)) { continue }
    foreach ($p in @(& $Impl[$id].Paths)) {
      if (-not $p -or "$p".StartsWith('!')) { continue }
      foreach ($root in $LocalApp, $CacheHome) {
        if ("$p".StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
          $covered[($root + '\' + "$p".Substring($root.Length + 1).Split('\')[0]).ToLowerInvariant()] = $true
        }
      }
    }
  }
  $script:Deep = $saved
  # 跳过时不输出路径的目标，以及 Temp 本身（由 temp 目标处理）
  foreach ($n in 'Temp', 'Mozilla', 'Google', 'Microsoft', 'BraveSoftware', 'CrashDumps') { $covered[(Join-Path $LocalApp $n).ToLowerInvariant()] = $true }
  $rows = foreach ($d in @(Sub-Dirs $LocalApp) + @(Sub-Dirs $CacheHome)) {
    if ($covered.ContainsKey($d.ToLowerInvariant())) { continue }
    $b = Size-Of @($d)
    if ($b -ge $UncoveredMinMB * 1MB) { [pscustomobject]@{ Bytes = $b; Path = $d } }
  }
  $rows = @($rows | Sort-Object Bytes -Descending)
  foreach ($r in $rows) { Info ('  {0,-9} {1}' -f (Human $r.Bytes), (Tilde $r.Path)) }
  if (-not $rows) { Ok '无' }
}

$script:Installed = $null
function App-Installed([string[]]$Names) {
  if ($null -eq $script:Installed) {
    $keys = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
      'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
      'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
    $script:Installed = @(Get-ItemProperty $keys -ErrorAction SilentlyContinue | ForEach-Object { $_.DisplayName } | Where-Object { $_ })
    $script:Installed += @(Get-AppxPackage -ErrorAction SilentlyContinue | ForEach-Object Name)
    $script:Installed += @(Sub-Dirs (Join-Path $ScoopDir 'apps') | ForEach-Object { Split-Path -Leaf $_ })
  }
  foreach ($n in $Names) {
    if ($n.Contains('/')) {
      if (Test-Path -LiteralPath (Join-Path $HomeDir $n.Replace('/', '\'))) { return $true }
      continue
    }
    if (Have $n) { return $true }
    # 中文名没有词边界，按子串匹配；ASCII 名按整词匹配，避免 nu 之类的短名误命中
    $rx = if ($n -match '^[\x00-\x7F]+$') { '(?<![\w])' + [regex]::Escape($n) + '(?![\w])' } else { [regex]::Escape($n) }
    if ($script:Installed | Where-Object { $_ -match $rx }) { return $true }
  }
  return $false
}

function Cmd-Leftovers {
  Section '已卸载应用的残留目录（确认无用后手动删除）'
  $any = $false
  foreach ($entry in $LeftoverMap) {
    $names, $paths = $entry -split '\|', 2
    $names = $names -split ';'
    if (App-Installed $names) { continue }
    foreach ($rel in $paths -split ';') {
      $p = Join-Path $HomeDir $rel.Replace('/', '\')
      if (-not (Test-Path -LiteralPath $p)) { continue }
      if (Electron-Busy $p) { continue }   # 目录正在被占用：应用改名或以其他方式安装
      Info ('  {0,-9} ~/{1,-40} （{2} 未安装）' -f (Human (Size-Of @($p))), $rel, $names[0])
      $any = $true
    }
  }
  if (-not $any) { Ok '无' }

  Section "超过 $StaleDays 天无写入的 Electron 配置目录"
  $any = $false
  $cutoff = (Get-Date).AddDays(-$StaleDays)
  foreach ($d in @(Electron-AppDirs)) {
    $newest = @(Get-Item -LiteralPath $d -Force) + @(Get-ChildItem -LiteralPath $d -Force -ErrorAction SilentlyContinue) +
      @(Get-ChildItem -LiteralPath $d -Force -Directory -ErrorAction SilentlyContinue | ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }) |
      Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $newest -or $newest.LastWriteTime -gt $cutoff) { continue }
    Info ('  {0,-9} {1,-40} 最后写入 {2:yyyy-MM-dd}' -f (Human (Size-Of @($d))), (Tilde $d), $newest.LastWriteTime)
    $any = $true
  }
  if (-not $any) { Ok '无' }
}

function Cmd-Scan {
  Section '空间概览'
  Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -gt 0 } | ForEach-Object {
    Info ('  {0}:  已用 {1}  可用 {2}' -f $_.Name, (Human $_.Used), (Human $_.Free))
  }
  Info "%TEMP% 总占用: $(Human (Size-Of @($env:TEMP)))"

  Section '清理目标（run 默认只执行 default 层；-Deep 时的数字见 scan -Deep）'
  Info ('{0,-11} {1,-8} {2,-9} {3,-8} {4}' -f '目标', '层级', '占用', '状态', '说明')
  [long]$total = 0
  foreach ($id in $Order) {
    if (-not (Target-Avail $id)) {
      Dim ('{0,-11} {1,-8} {2,-9} {3,-8} {4}' -f $id, $Tier[$id], '-', '不适用', $Desc[$id])
      continue
    }
    $m = Measure-Target $id
    $state = '可清理'
    if ($m.Skips) { $state = '有跳过' }
    elseif ($m.Bytes -lt 0) { $state = '未统计' }
    elseif ($m.Bytes -eq 0) { $state = '无数据' }
    $size = if ($m.Bytes -lt 0) { '-' } else { Human $m.Bytes }
    if ($Tier[$id] -eq 'default' -and $m.Bytes -gt 0) { $total += $m.Bytes }
    Info ('{0,-11} {1,-8} {2,-9} {3,-8} {4}' -f $id, $Tier[$id], $size, $state, $Desc[$id])
    if ($m.Skips) { Write-Host ('{0,-30} ↳ {1}' -f '', ($m.Skips -join '; ')) -ForegroundColor Yellow }
  }
  Info ''
  Info "default 层合计占用约 $(Human $total)（uv/pnpm/pre-commit/npm 等为修剪，实际释放可能更少）"

  Scan-Uncovered
  Cmd-Leftovers

  Section '仅报告'
  $thumbs = Get-ChildItem -LiteralPath (Join-Path $LocalApp 'Microsoft\Windows\Explorer') -Filter '*cache_*.db' -Force -ErrorAction SilentlyContinue
  Info ('缩略图 / 图标缓存: ' + (Human (($thumbs | Measure-Object Length -Sum).Sum)) + '（被资源管理器占用；用磁盘清理 cleanmgr 的缩略图项清理）')
  $vhds = @(Get-ChildItem -LiteralPath (Join-Path $LocalApp 'Packages') -Filter 'ext4.vhdx' -Recurse -Depth 3 -Force -ErrorAction SilentlyContinue) +
    @(Get-ChildItem -LiteralPath (Join-Path $LocalApp 'Docker\wsl') -Filter '*.vhdx' -Recurse -Force -ErrorAction SilentlyContinue)
  foreach ($v in $vhds) { Info "WSL 虚拟磁盘: $(Human $v.Length)  $(Tilde $v.FullName)（压缩需先 wsl --shutdown，建议手动执行）" }
  Info 'Electron / 浏览器的 Service Worker、IndexedDB、Local Storage 属于离线数据，不在清理范围内'
  Info ''
  Info '下一步: run 预览 default 层；run -Apply 执行；run wu dism 预览系统级清理（需要管理员）'
}

function Cmd-Run {
  $ids = New-Object System.Collections.Generic.List[string]
  $sel = if ($Targets) { $Targets } else { @('default') }
  foreach ($s in $sel) {
    switch ($s) {
      'all' { $Order | ForEach-Object { $ids.Add($_) } }
      { $_ -in 'default', 'opt' } { $Order | Where-Object { $Tier[$_] -eq $s } | ForEach-Object { $ids.Add($_) } }
      default { if (-not $Tier.ContainsKey($s)) { Die "未知目标: $s（用 list 查看）" }; $ids.Add($s) }
    }
  }
  $excl = @($Exclude | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
  foreach ($s in $excl) { if (-not $Tier.ContainsKey($s)) { Die "未知目标: $s" } }

  if ($script:Dry) { Write-Host 'dry-run：以下命令不会执行，加 -Apply 实际清理' -ForegroundColor Yellow }
  if ($Deep) { Write-Host '-Deep：scoop/uv/npm/go/cargo/pre-commit 使用更彻底的清理方式' -ForegroundColor Yellow }
  $done = @{}
  [long]$freed = 0
  foreach ($id in $ids) {
    if ($done.ContainsKey($id) -or $excl -contains $id) { continue }
    $done[$id] = $true
    Section $id
    if (-not (Target-Avail $id)) { Info '  不适用（未安装或无数据）'; continue }
    if (-not $script:Dry) { $before = (Measure-Target $id).Bytes; $script:Locked = 0 }
    try { & $Impl[$id].Clean } catch { Warn "$id 失败（继续）: $_"; $script:Failed = $true }
    if (-not $script:Dry) {
      if ($script:Locked) { Info "  $($script:Locked) 个文件被占用，已跳过" }
      $after = (Measure-Target $id).Bytes
      if ($before -gt $after) { $freed += $before - $after }
    }
  }
  Info ''
  if ($script:Dry) { Info '以上为预览。加 -Apply 执行。' }
  else { Ok "完成，估算释放 $(Human $freed)" }
  if ($script:Failed) { Err '部分命令失败，见上方警告'; exit 1 }
}

function Usage {
  $lines = Get-Content -LiteralPath $PSCommandPath -Encoding UTF8
  $start = [array]::IndexOf($lines, '<#') + 1
  $end = [array]::IndexOf($lines, '#>') - 1
  $lines[$start..$end] | ForEach-Object { Write-Host $_ }
}

# ---------------------------------------------------------------- main

switch ($Command) {
  'scan'      { Cmd-Scan }
  'list'      { Cmd-List }
  'run'       { Cmd-Run }
  'leftovers' { Cmd-Leftovers }
  { $_ -in '', 'help', '-h', '--help' } { Usage }
  default     { Usage; exit 2 }
}
