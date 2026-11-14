# termux-desktop

Termux 原生桌面环境，面向 Android/Termux 的通用 X11 桌面与 proot 应用集成方案。

> **状态：M0 到 M3 已在设备上验收。**
> `desk setup`、`start`、`stop`、`status`、`logs` 已经在 Termux 本体上确认显示、输入、停止后再次启动和桌面注销的行为。
> 容器相关的 `desk container init`、`desk app launch`、`desk app refresh` 和 `desk pkg` 已经实现，两个容器的应用都出现在同一个桌面菜单里。
> `desk install`、`desk uninstall`、`desk update` 和 `install.sh` 已经实现，第一个版本是 `0.0.1`，当前是 `0.6.0`。
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
desk update --version 0.6.0
desk uninstall          # 保留状态、日志和菜单条目
desk uninstall --purge  # 一并删除状态、日志、生成的菜单条目和字体偏好文件
desk version            # 已装版本、来源、安装目录
```

从仓库安装的副本记录为 `source=checkout`，从发布归档安装的记录为 `source=release:<tag>`。`desk update` 不替换仓库副本（`source=checkout`），要覆盖它需加 `--force`。更新成功后上一份留在 `$PREFIX/lib/termux-desktop.previous`，直到下一次成功更新。`desk setup` 仍然只涉及 Termux 包和本机字体偏好，和安装 `desk` 命令本身是两件事。

## M0 范围

命令（工作名，最终命名待定）：

```bash
desk setup
desk upgrade
desk font status
desk font remove
desk doctor [--vnc] [--container] [--font]
desk help
desk start
desk status
desk stop
desk logs
desk container init NAME
desk container list
desk container status NAME
desk app launch CONTAINER/APP [-- ARGS]
desk app refresh
desk app doctor [--container NAME]
desk pkg CONTAINER ARGS...
desk vnc setup
desk vnc start [--display :1] [--geometry 1280x720] [--password-file FILE]
desk vnc start --display :0 --mirror [--view-only]
desk vnc stop [--display :1]
desk vnc status [--display :1]
desk vnc web setup [--proxy rs|py|both]
desk vnc web start [--proxy rs|py] [--display :1] [--port 6080] [--host 127.0.0.1]
desk vnc web stop [--display :1]
desk vnc web status [--display :1]
desk install [--source DIR]
desk uninstall [--purge]
desk update [--check] [--version VERSION]
desk version [--vnc]
desk --version
```

VNC 是一个可选的显示后端：`desk vnc setup` 安装 `x11-repo`、`tigervnc` 与 `darkhttpd`（加 `--web` 会顺带装好 noVNC 页面与默认 WebSocket 代理），`desk vnc start` 用 `Xvnc` 在 `:1` 启动独立的 XFCE 会话（默认 `1280x720`、24 位、只绑 `127.0.0.1`、无密码）。要查看设备上正在跑的 `:0` 桌面，用 `desk vnc start --display :0 --mirror`（`x0vncserver` 挂到 termux-x11，画面跟随设备分辨率，键鼠输入会作用到设备桌面；加 `--view-only` 则只看不点，`desk vnc web start --display :0 --view-only` 可让浏览器在启动镜像时就用只读模式）；`:0` 不允许新起 `Xvnc`，其他 display 仍是独立会话。Termux 包不含 VNC viewer，需要从另一台设备经 SSH 隧道连接（`ssh -L 5901:127.0.0.1:5901 <设备>`，再用任意 VNC viewer 打开 `localhost:5901`）；需要密码时用 `--password-file` 提供 `vncpasswd` 生成的二进制文件。`desk vnc start` 的会话与 termux-x11 的 `:0` 并存，`desk status` 会列出全部会话，`desk stop --all` 停止全部 web 代理、镜像与会话。`desk vnc status` 汇总所有属于 VNC 的 display（独立会话、`:0` 镜像、web 代理），`desk vnc logs [--display :N] [--web]` 查看会话/镜像日志或代理日志，`desk vnc version` 打印 Xvnc/x0vncserver 版本与页面、代理的安装状态；`desk version` 默认也在 desk 首行与来源信息之后列出同一段 VNC 组件（未安装时只有一行 `VNC: not installed (optional).`），`desk version --vnc` 则只列 VNC 组件、等同 `desk vnc version`。

浏览器访问用 `desk vnc web setup` 安装 pinned 的 noVNC v1.7.0 页面与 WebSocket 代理（默认 `websockify_rs`，`--proxy py` 用 Python `websockify`，`--proxy both` 两个都装）。`desk vnc web start` 在会话未起时先启动 VNC（`--display :0` 时启动镜像），再在 `127.0.0.1:6080` 提供页面；rs 代理的 WebSocket 端口是 `6081`（页面 URL 自带 `?host=127.0.0.1&port=6081`），py 代理单端口。rs 模式的静态页由 `darkhttpd` 提供（`desk vnc setup` 会安装它），WebSocket 由 rs 代理转发；py 模式由 `websockify --web` 单进程同时服务页面与代理。`desk vnc web stop` 只停页面与代理，不停 VNC 会话；而 `desk vnc stop` 会连同该 display 的页面与代理一起停掉。默认只绑回环，`--host 0.0.0.0` 需显式指定并会打印警告；本版没有 TLS 与密码。代理选型、许可与内存预期见 [websockify 调研与基准](docs/websockify.md)。

只支持一个固定配置：

- Termux 本体
- Termux:X11，`DISPLAY=:0`
- XFCE
- APT 环境
- 桌面和 Firefox 跑在 Termux 本体；不启动 proot 做桌面，不做音频/GPU；VNC 使用 TigerVNC 作为可选接入（默认只绑回环、经 SSH 隧道），见 [决策记录](docs/decisions.md)

`desk setup` 安装桌面需要的固定包，然后按设备语言写入 CJK 字体偏好：

```text
x11-repo
termux-x11-nightly
xfce4
dbus
termux-am
```

包安装完成后，`desk setup` 按 Android 系统语言探测设备上实际存在的 CJK 字体家族，写入 `$PREFIX/etc/fonts/conf.d/63-cjk-prefer.conf`，并为其他 CJK 语言写 `lang` 规则；没有可用面、fontconfig 或目录缺失时只打印警告。文件内容由探测结果生成，换设备重新运行会按新设备重写。`desk font status` 只读地报告检测到的语言、当前偏好文件与设备上实际命中的字体家族；`desk font remove` 删除它，`desk uninstall --purge` 也会回收。

自动安装的边界：

- 先安装 `x11-repo`，刷新包索引，再安装其余包
- 只安装缺失的包，不执行 `pkg upgrade`
- `desk upgrade` 只升级候选版本来自 X11 仓库（apt origin 为 `termux-x11`）的已安装包；`desk setup` 仍然不升级
- 不安装 Termux:X11 Android App；它是 APK，由用户手动安装，`doctor` 检查并给下载地址
- 不安装 Firefox；需要浏览器时自行 `pkg install firefox`
- 不安装 proot、GPU 驱动、主题
- `desk start` 永远不安装依赖；缺依赖时直接报错并提示运行 `desk setup`

关键约束：

- `desk setup` 和 `desk start` 分离，启动桌面不会改系统包
- `desk upgrade` 与 `desk setup` 分离：需要升级 X11 仓库的包时由用户显式运行
- `desk start` 启动前刷新一次容器菜单；`proot-distro` 不存在时跳过，刷新失败只警告
- `desk stop` 只按会话进程组清理；清理后仍有残留就报错，不用 `pkill -f` 做全局兜底
- 状态写 JSON，日志写用户目录
- `start` 启动后退出，不做常驻 daemon
- 不修改 `.bashrc` 或 `.zshrc`
- 必须在 Termux 本体运行和验证

## 目标环境

- Termux 本体（GitHub / F-Droid 版）
- Termux:X11 App（优先 sharedUid 构建）
- XFCE
- Firefox（可选；M1 验收用，默认不安装）
- proot-distro（M2）
- 具体 Android 版本、CPU 架构和 GPU 由设备决定；只支持同架构的 proot 容器。

## 开发与检查

开发在 Ubuntu 环境进行（运行时在 Termux，不需要安装这些工具）：

```bash
PYTHONPATH=src:tests python3 -m unittest discover -s tests
ruff check src tests benchmarks/websockify
pylint src/termux_desk
```

规则配置在 `pyproject.toml`：ruff 只启用低噪声的语法/未使用名字/导入排序规则，pylint 只保留错误与文件规模检查（`too-many-lines` 等），风格规则不重复。

## 文档

- [架构](docs/architecture.md)
- [决策记录](docs/decisions.md)
- [路线图](docs/roadmap.md)
- [多语言与 CJK 显示](docs/i18n.md)
- [显示缩放调查](docs/display-scale.md)
- [浏览器访问 VNC 的代理调研](docs/websockify.md)
- [参考项目笔记](docs/reference.md)
- [未决问题](docs/open-questions.md)

## 非目标（当前）

- 把整个桌面塞进 proot/chroot
- 多桌面环境/窗口管理器
- Chromium / Google Chrome
- 编辑器 / IDE
- 音频
- GPU 加速
- Wayland
- 多 CPU 架构 / x86 应用
- 主题、图标、壁纸、AppStore
- 图形化安装器

## 参考与许可

参考项目：[sabamdarif/termux-desktop](https://github.com/sabamdarif/termux-desktop)

- 只参考功能和流程，不复制代码。
- 参考项目为 GPLv3；本项目没有复制其代码，采用 MIT。如果未来复制其代码片段再分发，需要按 GPLv3 重新评估。
- 本项目许可证：MIT，见 [LICENSE](LICENSE)。
