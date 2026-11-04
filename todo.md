# todo: Linux 双系统同步的剩余事项

Kali 迁移已在 2026-11-04 收尾。两台机器的说明见 [docs/arch.md](docs/arch.md) 和 [docs/kali.md](docs/kali.md)；两台 Linux 共用的同步规则在 [workflows_zh-CN.md](docs/workflows_zh-CN.md) 第 6 节，常见陷阱在 [reference_zh-CN.md](docs/reference_zh-CN.md) 6.5。本文件只记录还没做的事，处理完后删除。

## 2. 工具和规则的改进（走 dev，有需要时再做）

- [ ] **`/etc` 备份跳过和软件包原样一致的文件**：`extra` / `secret` 中直接列出的文件，如果属于某个软件包且没有被改过（pacman 没有 `[modified]` 标记；dpkg 的 md5 相同），就跳过。现在 Kali 会收入 5 个这样的文件（kali.md 第 3 节）。注意：只有 root 可读、没能核对校验和的文件必须当作"已修改"保留；已经在 `system/<发行版>` 中的条目不会自动消失，要另外列清单删除。
- [ ] 想在 Kali 上换回 Konsole 时：新增 `konsole` 标签（按能否找到 konsole 检测），把 `.config/konsolerc` 和 `.local/share/konsole` 从 `kde` 移到这个标签下。Arch 合并后要重新 `chezmoi init`。
- [ ] Windows 系统配置备份：用 `system/windows/` 保存导出的注册表等系统设置（目录已预留，功能尚未实现）。
- [ ] 检测更多标签（例如 `xfce`，或者手动设置的本机标签），有实际用途时再加。

## 3. 可选

- [ ] `dotfiles status` 的 "dpkg / ucf leftovers" 在 Kali 上列出 9 个文件，都不需要处理（见 kali.md 第 3 节），属于噪音。要消除的话：跳过有 diversion 的文件，以及 `ca-certificates.conf.dpkg-old`。

## 4. 已完成（摘要，细节见 git 历史）

- 0.4.0：平台归属、环境标签、`/etc` 按发行版分目录。Kali 迁移（2026-11-03，PR #10）。
- 0.5.0（#11）：apt 软件包清单，dpkg 感知的 `/etc` 备份，程序标签，restore 的 hooks 只改自己的仓库。`.zshrc` 改为明文，token 拆到加密的 `secrets.zsh`。
- 0.5.1（#12）：修剪，通过 `.chezmoiremove` 在两台机器上停用不用的目标。
- 0.5.2（#13）、0.5.4：`/etc` 备份不再重新加密没有变化的文件（0.5.4 修复了 0440 / 0640 和 umask 002 的情况）。
- 0.5.3：apply 的演练带上 `--force`。
- 0.5.4：apply 部署 fcitx5 的配置后让 fcitx5 重新加载（在 Arch 上用 D-Bus `Save` 验证过：只用 `fcitx5-remote -r` 不够）。workflows 第 3 节改为在主检出中切换到 dev；同步规则和常见陷阱移进 workflows 第 6 节和 reference 6.5；新增 docs/arch.md。
- Kali 收尾（2026-11-04）：`scripts/kali.sh`，zellij 在 GNOME 上用 `wl-copy`，fcitx5 用中文写配置，`/etc` 首次备份。Arch 收尾：拉取并部署，fcitx5 的 `notifications.conf` 已部署并验证写回，删除了 `~/.local/share/mamba`。一次性脚本 `scripts/kali.sh`、`scripts/arch.sh` 执行完后已删除。
