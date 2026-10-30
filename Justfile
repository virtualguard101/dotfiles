# 常用命令的快捷方式（可选；不装 just 时直接用 uv run --frozen --no-dev dotfiles …）。
# Windows 上用 PowerShell 7 执行配方。
set windows-shell := ["pwsh", "-NoLogo", "-NoProfile", "-Command"]

dot := "uv run --frozen --no-dev dotfiles"

# 列出所有配方
default:
    @just --list

# 检测系统环境、工具链与加密配置（只读）
doctor:
    {{ dot }} doctor

# 重装后恢复；默认只显示计划，例如 just restore --apply
restore *args:
    {{ dot }} restore {{ args }}

# 备份状态（只读）
status:
    {{ dot }} status

# 备份；例如 just backup -n / just backup --commit
backup *args:
    {{ dot }} backup {{ args }}

# 安全检查
check:
    {{ dot }} check

# 敏感文件权限审计；just audit --apply 收紧
audit *args:
    {{ dot }} audit {{ args }}

# 缓存清理；例如 just clean scan / just clean run --apply
clean *args:
    {{ dot }} clean {{ args }}

# 运行测试（会安装 dev 依赖 pytest）
test *args:
    uv run --frozen pytest {{ args }}
