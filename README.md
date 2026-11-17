# termux-desktop

Termux 原生桌面环境，面向 Android/Termux 的通用 X11 桌面与 proot 应用集成方案。

> **状态：M0 到 M3 已在设备上验收。**
> `desk setup`、`start`、`stop`、`status`、`logs` 已经在 Termux 本体上确认显示、输入、停止后再次启动和桌面注销的行为。
> 容器相关的 `desk container init`、`desk app launch`、`desk app refresh` 和 `desk pkg` 已经实现，两个容器的应用都出现在同一个桌面菜单里。
> `desk install`、`desk uninstall`、`desk update` 和 `install.sh` 已经实现，第一个版本是 `0.0.1`，当前是 `0.7.1`。
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
desk update --version 0.7.1
desk uninstall          # 保留状态、日志和菜单条目
desk uninstall --purge  # 一并删除状态、日志、生成的菜单条目、字体偏好、配置与 profiles
desk version            # 已装版本、来源、安装目录
```

从仓库安装的副本记录为 `source=checkout`，从发布归档安装的记录为 `source=release:<tag>`。`desk update` 不替换仓库副本（`source=checkout`），要覆盖它需加 `--force`。更新成功后上一份留在 `$PREFIX/lib/termux-desktop.previous`，直到下一次成功更新。`desk setup` 仍然只涉及 Termux 包和本机字体偏好，和安装 `desk` 命令本身是两件事。

从 0.7.0 升到 0.7.1：先 `desk update`，再 `desk setup`（补齐 PyYAML），最后重启会话让 `vnc`、profile 与配置默认值生效（`desk stop --all` 后重新 `desk start`）。0.7.1 的配置文件可以包含 `vnc`/`ime`/`fonts` 段；回滚到 0.7.0 前要先移走 `~/.config/termux-desktop/config.yaml` 与 `profiles/`，因为 0.7.0 会把未知段当作错误拒绝加载。

## M0 范围

命令（工作名，最终命名待定）：

```bash
desk setup [--ime]
desk --profile NAME config path
desk config show
desk config check
desk config init [--force]
desk config set app CONTAINER/APP program PROGRAM
desk config set app CONTAINER/APP args-prefix [--] ARG...
desk config set app CONTAINER/APP environment NAME VALUE
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
desk app doctor [--container NAME] [--start [--timeout SECONDS]]
desk pkg CONTAINER ARGS...
desk vnc setup
desk vnc start [--display :1] [--geometry 1280x720] [--password-file FILE]
desk vnc resize WIDTHxHEIGHT [--display :1]
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

VNC 是一个可选的显示后端：`desk vnc setup` 安装 `x11-repo`、`tigervnc` 与 `darkhttpd`（加 `--web` 会顺带装好 noVNC 页面与默认 WebSocket 代理），`desk vnc start` 用 `Xvnc` 在 `:1` 启动独立的 XFCE 会话（默认 `1280x720`、24 位、只绑 `127.0.0.1`、无密码）。要查看设备上正在跑的 `:0` 桌面，用 `desk vnc start --display :0 --mirror`（`x0vncserver` 挂到 termux-x11，画面跟随设备分辨率，键鼠输入会作用到设备桌面；加 `--view-only` 则只看不点，`desk vnc web start --display :0 --view-only` 可让浏览器在启动镜像时就用只读模式）；`:0` 不允许新起 `Xvnc`，其他 display 仍是独立会话。分辨率用 `desk vnc resize 1024x600` 调整；当前实现是重启该 VNC 会话（应用会关闭），因为 Xvnc 1.16.2 在本构建上不支持运行中改 framebuffer（SetDesktopSize 会崩溃，RandR 只有固定模式）。`:0` 镜像跟随手机分辨率，不能 resize。Termux 包不含 VNC viewer，需要从另一台设备经 SSH 隧道连接（`ssh -L 5901:127.0.0.1:5901 <设备>`，再用任意 VNC viewer 打开 `localhost:5901`）；需要密码时用 `--password-file` 提供 `vncpasswd` 生成的二进制文件。`desk vnc start` 的会话与 termux-x11 的 `:0` 并存，`desk status` 会列出全部会话，`desk stop --all` 停止全部 web 代理、镜像与会话。`desk vnc status` 汇总所有属于 VNC 的 display（独立会话、`:0` 镜像、web 代理），`desk vnc logs [--display :N] [--web]` 查看会话/镜像日志或代理日志，`desk vnc version` 打印 Xvnc/x0vncserver 版本与页面、代理的安装状态；`desk version` 默认也在 desk 首行与来源信息之后列出同一段 VNC 组件（未安装时只有一行 `VNC: not installed (optional).`），`desk version --vnc` 则只列 VNC 组件、等同 `desk vnc version`。

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

输入法（可选）：

```bash
desk setup --ime
```

会额外安装 `ibus`、`ibus-libpinyin`、`libdbusmenu-gtk3`。重启桌面会话后，`desk start` 只在检测到 ibus 时才把 `GTK_IM_MODULE=ibus`、`QT_IM_MODULE=ibus`、`XMODIFIERS=@im=ibus` 写进会话环境，并在会话自己的 D-Bus 总线里启动 `ibus-daemon`（带 GTK 面板）；每个 X display 一个 daemon，会话退出时随总线一起结束。`Ctrl+空格` 切换中英文。容器应用通过 XIM 使用同一套输入法（`GTK_IM_MODULE=xim`、`XMODIFIERS=@im=ibus`），终端里也能直接输入中文；容器内的预编辑显示取决于应用本身。浏览器经 noVNC 连接时，输入依赖浏览器自身的输入法，能力有限。不安装输入法时桌面行为不变。fcitx5 已评估：候选窗在 Termux 的 X11 会话里无法显示，desk 不采用，见 [为何不采用 fcitx5](docs/fcitx5.md)。

配置文件：

```bash
desk --profile NAME config path
desk config show
desk config check
desk config init [--force]
desk config set app CONTAINER/APP program PROGRAM
desk config set app CONTAINER/APP args-prefix [--] ARG...
desk config set app CONTAINER/APP environment NAME VALUE
desk config unset app CONTAINER/APP program
desk config unset app CONTAINER/APP args-prefix
desk config unset app CONTAINER/APP environment NAME
desk config set vnc display VALUE
desk config set vnc geometry VALUE
desk config set ime enabled auto|true|false
desk config set fonts language auto|zh-Hans|zh-Hant|zh-Hant-HK|ja|ko
desk config set fonts sans_serif FAMILY
desk config unset fonts sans_serif
desk config profiles
```

`--profile NAME` 可以放在子命令前后（也可以设置 `DESK_PROFILE=NAME`），只在第一个 `--` 之前生效；要把 `--profile` 作为参数传给容器应用，放在 `--` 之后。后续命令、启动的会话以及从菜单启动的容器应用都会使用同一份 profile；profile 是独立的 YAML 文件，路径为 `$XDG_CONFIG_HOME/termux-desktop/profiles/NAME.yaml`，默认配置仍是 `config.yaml`。还没有文件时先运行 `desk --profile NAME config init`；加载一个不存在的 profile 会直接报错并提示；`desk start` 也会在启动会话前检查选中的 profile 是否存在。`desk config profiles` 列出活动 profile 与已有 profile 文件。v1 只切换 desk 自己的配置段，不切换窗口管理器，也不复制 XFCE 配置。

`desk config init` 把内置模板原样复制到用户配置路径（带注释；已存在时需 `--force`，覆盖前会备份为 `config.yaml.bak`，profile 下为 `profiles/NAME.yaml.bak`）；`desk config set app ...` / `desk config unset app ...` 修改或删除 apps 里的覆盖项（先校验新内容，再重写 apps 段并备份原文件；apps 段之外的注释保留，设置相同值时不重写并打印 `Nothing changed`；以 `-` 开头的 program/environment 值前加 `--`，args-prefix 只在第一个 `--` 处停止解析，后面的 `--` 原样传入；文件必须是 UTF-8，CRLF 原样保留；所有写入都是原子的，并拒绝符号链接、非普通文件与超过 64 KiB 的结果）；`config set vnc/ime/fonts ...` 修改对应段；`config unset vnc/ime <键>` 把该键重置为内置默认值并写回文件，`config unset fonts <sans_serif|serif|monospace>` 则删除该键（回到自动探测），`config unset fonts language` 重置为 `auto`；`desk config path` 打印当前配置路径（选中 profile 时为 `profiles/NAME.yaml`）：`$XDG_CONFIG_HOME/termux-desktop/config.yaml`，未设置时是 `~/.config/termux-desktop/config.yaml`。用户文件不存在时，desk 解析包内自带的 `src/termux_desk/default_config.yaml`（带注释，等价于旧的默认行为）；用户文件存在时完全替代内置模板，不合并。`desk config show` 打印最终生效的内容，`desk config check` 校验语法与字段，出错时以非零状态退出。`desk doctor` 也会检查这个文件。

`desk setup` 会尝试把 PyYAML 6.0.3 装进用户 site-packages（`pip install --user pyyaml==6.0.3`），不进入固定包列表；安装失败只打印 warning，不阻断字体、输入法与 VNC 步骤。配置不可用（缺 PyYAML、YAML 损坏或读取失败）时的行为按命令区分：

| 情况 | `config` / `doctor` | `desk app launch` | `desk vnc start` / `web start` | `desk vnc stop` / `resize` / `web stop` / `web status` |
|---|---|---|---|---|
| 选中的 profile 不存在或非法 | 报错 | 报错 | 报错 | 报错 |
| 有配置文件但读不了 | 报错 | 报错 | 打印 `Using the built-in VNC defaults: ...` 后用默认值继续 | 报错，不猜目标 |
| 没有用户文件 | 使用内置模板（需 PyYAML） | 使用内置默认值 | 使用内置默认值（缺 PyYAML 也回退） | 用默认值；缺 PyYAML 时 `stop` 仍报错 |

即：只有“没有配置文件”或“配置不可读但命令是启动/查询”才回退；破坏性命令（停、改分辨率、停 web）不会在配置错误时猜测目标。

顶层接受 `version: 1` 以及可选的 `apps:`、`vnc:`、`ime:`、`fonts:` 段。配置是默认值：命令行显式给出的参数优先，其次读配置，最后才是内置默认。

`vnc` 段是 `desk vnc start`、`vnc web start`、`vnc stop`、`vnc resize` 的默认 display 与连接参数：

- `display`：VNC 会话默认 display，默认 `":1"`；`:0` 只用于 `--mirror`
- `geometry`、`depth`：新会话的分辨率与色深（16/24/32）
- `host`、`port`、`proxy`（rs/py）、`view_only`：`desk vnc web start` 的默认值；`--mirror` 不显式指定 `--view-only` 时也读 `view_only`。`view_only: true` 配非 `:0` display 时，web start 会忽略它并打印警告；命令行显式 `--view-only` 仍按错误处理
- 任何显式命令行参数都会覆盖这里的值

`ime` 段只有 `enabled`：`auto`（默认）、`true` 或 `false`。`auto` 与 `true` 都要求 ibus 已安装（`desk setup --ime`）；`false` 即使装了也不启用。配置损坏时按 `auto` 处理，避免登录被配置问题阻断。

`fonts` 段控制 `desk setup` 写出的 fontconfig 偏好：

- `language`：`auto`（按设备语言）或 `zh-Hans`、`zh-Hant`、`zh-Hant-HK`、`ja`、`ko`
- `sans_serif`、`serif`、`monospace`：直接覆盖该角色的字体家族；指定的家族不存在时会打印警告并退回探测结果
- 覆盖只影响写入的偏好文件，不安装字体

`apps` 的键是 `CONTAINER/APP`，值可以包含：

- `program`：替换桌面条目 Exec 里的程序（容器内路径），用于给容器应用套 wrapper
- `args_prefix`：插在程序名之后的固定参数
- `environment`：追加或覆盖传给 proot 的环境变量

```yaml
version: 1

vnc:
  display: ":1"          # 引号：: 开头必须加引号
  geometry: "1280x720"
  depth: 24
  host: "127.0.0.1"      # 只监听回环；改 0.0.0.0 前先想清楚
  port: 6080
  proxy: rs              # rs 需要 darkhttpd；py 用 websockify wheel
  view_only: false

ime:
  enabled: auto          # auto | true | false

fonts:
  language: auto         # auto 或 zh-Hans/zh-Hant/zh-Hant-HK/ja/ko
  # sans_serif: "Noto Sans CJK SC"
  # serif: "Noto Serif CJK SC"
  # monospace: "Noto Sans Mono CJK SC"

apps:
  "ubuntu-desktop-test/com.microsoft.VSCode":
    # root 运行 Electron 需要 --no-sandbox；数据目录固定到容器内
    program: /usr/share/code/code
    args_prefix:
      - --no-sandbox
      - --user-data-dir=/root/.config/Code
    environment:
      GTK_IM_MODULE: xim
```

这三个字段就是应用级启动包装：`program` 替换 Exec 的第一个 token（可以指向容器内的 wrapper 脚本），`args_prefix` 插在程序之后，`environment` 追加到 proot 的 `-e`；菜单项本身不用改。`desk app launch` 与从菜单启动都会套用这些覆盖。要确认应用不只会被“找到”而且能真正启动，用 `desk app doctor --start`：它按 app launch 的命令逐个启动应用，超时（默认 8 秒）后结束进程组；仍在运行或退出码 0 算启动成功，非零退出会打印最后的 stderr。探针还会读 X 的 `_NET_CLIENT_LIST`，报告这段时间是否出现了新窗口；没有窗口只提示，不算失败（后台服务类应用本来就没有窗口）。

解析用 PyYAML 的 `safe_load`；重复键会直接报错，未知字段也会报错。desk 不会自动改写配置文件；只有显式运行 `desk config init/set/unset` 才会写入，写入前先校验、原文件备份为 `config.yaml.bak`，apps 段之外的注释保留。`geometry`、`display`、`:1` 这类值在 YAML 里需要加引号。内置模板解析失败视为安装损坏并直接报错；用户文件有错时 `desk app launch` 会停止并报出原因。

自动安装的边界：

- 先安装 `x11-repo`，刷新包索引，再安装其余包
- 只安装缺失的包，不执行 `pkg upgrade`
- `desk upgrade` 只升级候选版本来自 X11 仓库（apt origin 为 `termux-x11`）的已安装包；`desk setup` 仍然不升级
- 不安装 Termux:X11 Android App；它是 APK，由用户手动安装，`doctor` 检查并给下载地址
- 不安装 Firefox；需要浏览器时自行 `pkg install firefox`
- 输入法默认不安装；需要时运行 `desk setup --ime`（只支持 ibus，fcitx5 不受支持）
- 安装 PyYAML 作为配置层依赖（用户 site-packages，不进入固定包列表）
- 不安装 proot、GPU 驱动，也不默认下载主题资产；`desk theme` 只下载用户清单里声明的资产
- `desk start` 永远不安装依赖；缺依赖时直接报错并提示运行 `desk setup`

关键约束：

- `desk setup` 和 `desk start` 分离，启动桌面不会改系统包
- `desk upgrade` 与 `desk setup` 分离：需要升级 X11 仓库的包时由用户显式运行
- `desk start` 启动前刷新一次容器菜单；`proot-distro` 不存在时跳过，刷新失败只警告
- `desk stop` 只按会话进程组清理；清理后仍有残留就报错，不用 `pkill -f` 做全局兜底
- 状态写 JSON，日志写用户目录
- desk 不自动改写 `config.yaml`；只有显式 `config init/set/unset` 会写，且先校验、备份、原子替换
- `start` 启动后退出，不做常驻 daemon
- 不修改 `.bashrc` 或 `.zshrc`
- 必须在 Termux 本体运行和验证

## 主题（可选）

主题是一份 YAML 清单，不是安装器；desk 只负责校验、下载资产并写进正在运行的 XFCE 会话，默认不携带任何资产。

```bash
desk theme list                 # 列出预设与用户主题
desk theme show stock           # 查看清单内容
desk theme init mine            # 在 ~/.config/termux-desktop/themes/mine.yaml 建模板
desk theme apply mine           # 安装缺失资产、快照旧值、写 xfconf、重启 xfsettingsd/xfwm4
desk theme reset                # 恢复上一次 apply 前的设置
```

清单在 `~/.config/termux-desktop/themes/<name>.yaml`，`presets/` 下的清单随 desk 安装：

```yaml
theme: mine
components:
  gtk: Adwaita
  xfwm: Default
  icons: Adwaita
  wallpaper: wall.png
fonts:
  default: "DejaVu Sans"
assets:
  - kind: wallpaper
    target: wall.png
    url: https://example.invalid/wall.png
    sha256: "..."
  - kind: font
    target: DejaVuSans.ttf
    path: /data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf
```

- `components` 支持 `gtk`、`xfwm`、`icons`、`cursors`、`wallpaper`；`fonts` 支持 `android:Family`、本地路径或 URL。缺组件或字体在 apply 前直接报错。
- 资产只允许 HTTPS，单文件上限 64 MiB。清单给了 `sha256` 就强制校验；没给则首次必须加 `--allow-unverified`，desk 会把实际哈希记进 state，之后同一 URL 强制比对。
- 安装位置：主题/图标进 `~/.local/share/themes` 与 `~/.local/share/icons`（并建 `~/.themes`、`~/.icons` 兼容软链），字体进 `~/.fonts`，壁纸进 `~/.local/share/termux-desktop/assets/<theme>/wallpapers`。容器应用通过已有的 HOME bind 直接看到这些路径，不需要额外绑定。
- `reset` 只恢复快照，不删除已下载的资产。桌面必须在运行（`desk start`）；通过 VNC 看桌面时，apply 后要重启对应 VNC 会话才会重读设置。
- 已知限制：只设置外观相关的名字与值（GTK 主题、xfwm4 主题、图标、光标、壁纸、字体）；不修改 xfwm4 的窗口按钮布局（`/general/button_layout`）、标题对齐、合成器/阴影选项、窗口规则和快捷键，也不修改面板的位置、大小、插件与启动器（xfce4-panel 配置）。容器内 GTK 版本差异不追完全一致；目前只随包提供 `stock` 一个预设。

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
- [为何不采用 fcitx5](docs/fcitx5.md)
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
