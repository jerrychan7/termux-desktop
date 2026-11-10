# termux-desktop

Termux 原生桌面环境，面向 Android/Termux 的通用 X11 桌面与 proot 应用集成方案。

> **状态：M0 到 M3 已在设备上验收。**
> `desk setup`、`start`、`stop`、`status`、`logs` 已经在 Termux 本体上确认显示、输入、停止后再次启动和桌面注销的行为。
> 容器相关的 `desk container init`、`desk app launch` 和 `desk app refresh` 已经实现，两个容器的应用都出现在同一个桌面菜单里。
> `desk install`、`desk uninstall`、`desk update` 和 `install.sh` 已经实现，第一个版本是 `0.0.1`。
> 运行时目标是 Termux 本体。

## 目标

在 Termux 本体上运行一个原生 XFCE 桌面：

- 桌面和浏览器直接跑在 Termux 里，不经过 proot
- 先用 Firefox 作为唯一浏览器
- 后续把 proot 容器中的 GUI 应用接入同一个桌面
- 一个桌面，多个应用容器；容器只是应用来源
- 参考 [sabamdarif/termux-desktop](https://github.com/sabamdarif/termux-desktop) 的流程和思路，不复制其代码

## 核心原则

1. **桌面只有一个**：Termux:X11 + XFCE，不存在“容器里的桌面”。
2. **容器只提供应用**：应用可以装在不同 proot 容器中，GUI 都显示在同一个 X server 上。
3. **先窄后宽**：先让 XFCE 稳定启停，再加 Firefox、proot、多容器。
4. **Python 负责决策，Bash 只负责启动和收尾**：主逻辑用 Python；Bash 只作为生成的薄会话脚本，负责环境变量、启动 X server 和退出清理。
5. **不做完整发行版**：不追求多 DE、主题商店、AppStore、复杂的安装器分支。
6. **不共享 home，容器目录挂载由项目统一安排**：每个容器使用自己的 home；容器里哪些目录挂载到哪里、路径如何约定，由项目统一安排；用户可以在这些约定之上绑定自定义目录。
7. **不把 proot 当安全沙箱**：proot 只隔离应用和发行版环境，不是安全边界。

## 分阶段计划

| 阶段 | 内容 | 验收 |
|---|---|---|
| **M0** | `desk setup` + `doctor` + `start/stop/status/logs`，安装固定依赖并启动 XFCE | 依赖能装上、桌面能显示、能干净退出、能再次启动 |
| **M1** | 加入原生 Firefox | 浏览器能正常访问网页 |
| **M2** | 一个 proot 容器 + 手写 `xterm` 菜单项 | 容器 GUI 能显示在 XFCE 上 |
| **M3** | 多容器 + 自动扫描 `.desktop` | 两个容器的应用都出现在同一个桌面菜单 |

M0 到 M3 的命令都已经实现。M3 之后的工作见 [路线图](docs/roadmap.md)。

## 安装

从仓库安装（不联网）：

```bash
pkg install git
git clone https://github.com/jerrychan7/termux-desktop
cd termux-desktop
./install.sh
```

不保留仓库、直接安装最新版本：

```bash
curl -fsSL https://raw.githubusercontent.com/jerrychan7/termux-desktop/main/install.sh | bash
```

`install.sh` 只做三件事：下载发布归档、解压、调用 `desk install --source`。复制文件、写版本记录都在 `desk install` 里。

安装后的布局：

```text
$PREFIX/bin/desk                         命令入口
$PREFIX/lib/termux-desktop/src/          Python 包
$PREFIX/lib/termux-desktop/templates/    会话脚本模板
$PREFIX/lib/termux-desktop/VERSION       版本和来源
```

升级和卸载：

```bash
desk update --check     # 只打印已装版本和最新版本
desk update             # 装最新版本
desk update --version 0.0.1
desk uninstall          # 保留状态、日志和菜单条目
desk uninstall --purge  # 一并删除状态、日志和生成的菜单条目
desk version            # 已装版本、来源、安装目录
```

从仓库安装的副本记录为 `source=checkout`，从发布归档安装的记录为 `source=release:<tag>`。`desk update` 不替换仓库副本（`source=checkout`），要覆盖它需加 `--force`。更新成功后上一份留在 `$PREFIX/lib/termux-desktop.previous`，直到下一次成功更新。`desk setup` 仍然只安装 Termux 包，和安装 `desk` 命令本身是两件事。

## M0 范围

命令（工作名，最终命名待定）：

```bash
desk setup
desk doctor
desk help
desk start
desk status
desk stop
desk logs
desk container init NAME
desk app launch CONTAINER/APP [-- ARGS]
desk app install CONTAINER PKG...
desk app refresh
desk install [--source DIR]
desk uninstall [--purge]
desk update [--check] [--version VERSION]
desk version
desk --version
```

只支持一个固定配置：

- Termux 本体
- Termux:X11，`DISPLAY=:0`
- XFCE
- APT 环境
- 桌面和 Firefox 跑在 Termux 本体；不启动 proot 做桌面，不做音频/GPU/VNC

`desk setup` 只安装桌面需要的固定包：

```text
x11-repo
termux-x11-nightly
xfce4
dbus
termux-am
firefox
```

自动安装的边界：

- 先安装 `x11-repo`，刷新包索引，再安装其余包
- 只安装缺失的包，不执行 `pkg upgrade`
- 不安装 Termux:X11 Android App；它是 APK，由用户手动安装，`doctor` 检查并给下载地址
- 不安装 proot、GPU 驱动、主题
- `desk start` 永远不安装依赖；缺依赖时直接报错并提示运行 `desk setup`

关键约束：

- `desk setup` 和 `desk start` 分离，启动桌面不会改系统包
- `desk stop` 只按会话进程组清理；清理后仍有残留就报错，不用 `pkill -f` 做全局兜底
- 状态写 JSON，日志写用户目录
- `start` 启动后退出，不做常驻 daemon
- 不修改 `.bashrc` 或 `.zshrc`
- 必须在 Termux 本体运行和验证

## 目标环境

- Termux 本体（GitHub / F-Droid 版）
- Termux:X11 App（优先 sharedUid 构建）
- XFCE
- Firefox（M1）
- proot-distro（M2）
- 具体 Android 版本、CPU 架构和 GPU 由设备决定；只支持同架构的 proot 容器。

## 文档

- [架构](docs/architecture.md)
- [决策记录](docs/decisions.md)
- [路线图](docs/roadmap.md)
- [参考项目笔记](docs/reference.md)
- [未决问题](docs/open-questions.md)

## 非目标（当前）

- 把整个桌面塞进 proot/chroot
- 多桌面环境/窗口管理器
- Chromium / Google Chrome
- 编辑器 / IDE
- VNC
- 音频
- GPU 加速
- Wayland
- 多 CPU 架构 / x86 应用
- 主题、图标、壁纸、AppStore
- 图形化安装器

## 参考与许可

参考项目：[sabamdarif/termux-desktop](https://github.com/sabamdarif/termux-desktop)

- 只参考功能和流程，不复制代码。
- 参考项目为 GPLv3；如果未来复制其代码片段再分发，需要重新评估本项目的许可证。
- 本项目许可证：**待定**。
