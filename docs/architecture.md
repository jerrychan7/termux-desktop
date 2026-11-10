# 架构

> 状态：M0 和 M1 已实现并在设备上验收；M2 和 M3 的容器应用已手工验证。
> 本文描述目标架构、M0 的会话启动顺序，以及 M2/M3 中 proot 应用的接入方式。

## 1. 术语

- **会话（session）**：一次桌面运行实例，包含 X server、XFCE 和相关进程。
- **会话管理器**：`desk` 命令，负责启动、停止、检查状态和清理会话；不负责画界面。
- **容器（container）**：一个 proot rootfs，例如 `ubuntu-desktop-test`。
- **应用（app）**：容器内或 Termux 原生的 GUI 程序。应用属于某个容器，或属于宿主。
- **X server**：`termux-x11` 进程，监听 `:0`。
- **Termux:X11 App**：Android 端的显示/输入前端，不是 X server。

## 2. 目标架构

```mermaid
flowchart TB
  subgraph Android["Android"]
    XApp["Termux:X11 App<br/>显示 / 输入"]
  end

  subgraph Host["Termux 本体"]
    Desk["desk<br/>Python 会话管理器"]
    Session["session.sh<br/>生成的薄启动脚本"]
    X["termux-x11 :0<br/>X server"]
    WM["XFCE"]
    FF["Firefox（原生）"]

    subgraph C1["proot: ubuntu-desktop-test"]
      App1["GUI app<br/>例如 xterm"]
    end

    subgraph C2["proot: 其他容器（后续）"]
      App2["GUI app"]
    end

    State["session.json / logs<br/>运行状态"]
  end

  Desk -->|生成并启动| Session
  Session --> X
  Session --> WM
  Desk --> State
  X <-->|X11 协议| XApp
  WM --> FF
  App1 -->|"DISPLAY=:0"| X
  App2 -->|"DISPLAY=:0"| X
```

核心边界：

- 桌面只有一个，所有 GUI 客户端连同一个 X server。
- 容器不是桌面边界，只是应用来源。
- 切换容器只影响某个应用从哪个 rootfs 启动，不影响桌面本身。

## 3. M0：安装依赖并启动 XFCE

M0 包含两部分：

1. `desk setup` 安装固定的 Termux 依赖。
2. `desk start` 启动 XFCE 会话。

`desk start` 永远不安装依赖。

`desk start --color` 控制启动进度和错误输出的 ANSI 着色（`auto`、`always`、`never`，默认 `auto`）；成功输出只高亮 `Starting` 和 `running`。

```mermaid
sequenceDiagram
  autonumber
  actor U as 用户
  participant D as desk
  participant P as pkg
  participant S as session.sh
  participant X as termux-x11
  participant A as Termux X11 App
  participant E as XFCE

  U->>D: desk setup
  D->>D: 检查固定依赖
  alt 有缺失
    D->>P: pkg install -y x11-repo（如缺失）
    D->>P: pkg update
    D->>P: pkg install -y 其余缺失包
    P-->>D: 安装完成
  end
  D-->>U: 安装结果

  U->>D: desk start
  D->>D: 检查依赖；缺失则退出
  D->>S: 生成并启动（新进程组）
  S->>X: termux-x11 :0
  S->>A: am start MainActivity
  A-->>X: 连接并显示
  S->>E: dbus-launch --exit-with-session startxfce4
  E-->>U: 桌面窗口

  U->>D: desk stop
  D->>S: 终止进程组
  S->>X: 退出
  S->>E: 退出
```

M0 的安装范围：

- 只安装：`x11-repo`、`termux-x11-nightly`、`xfce4`、`dbus`、`termux-am`
- 只安装缺失的包，不执行 `pkg upgrade`
- 不安装 Termux:X11 Android App；APK 由用户手动安装，`doctor` 检查并给下载地址
- 不安装 Firefox、proot、GPU 驱动、主题

M0 进程模型：

- `desk start` 用 `start_new_session=True` 启动 `session.sh`，记录 PGID。
- 如果同一个会话仍在运行，`desk start` 不报错也不重启：它执行一次 `am start` 让 Termux:X11 App 回到前台，然后按原状态报告。Android 会把 App 移出前台，但 `termux-x11` 和 XFCE 还在，这种情况下需要的是把显示接回来，而不是一个新会话。
- `desk stop` 只对 PGID 发 `SIGTERM`，超时后再 `SIGKILL`。
- `desk stop` 在进程组清空或 state 已 stale 后，只有确认没有 X server 占用 `:0` 才删除陈旧的 X11 socket/lock；仍占用或删除失败时保留文件。
- 如果进程组清理后仍有残留，`desk stop` 报错并保留状态，不使用 `pkill -f` 全局搜索进程。
- `desk status` 读取 `session.json`，用进程组和会话标识检查存活。
- `desk logs` 读取会话日志：错误关键词红色、警告关键词黄色、`[desk]` 标记仅自身青色，成功和普通消息不染色，`=== desk ... ===` 分隔标题加粗、斜体、下划线、亮白；`--color` 取值为 `auto`、`always`、`never`，默认 `auto`。
- `desk logs --desk` 读取命令日志：时间戳灰色，`failed` 和非零 `finished/exited with status` 短语红色，`interrupted`/`warn`/`warning` 黄色，其余消息保持默认前景色。
- 不做常驻 supervisor；`desk start` 启动后退出。
- 状态记录 boot id、会话标识和会话进程的启动时间；`stop` 和 `status` 先验证这些事实，验证不了就拒绝操作并保留状态。
- `start` 和 `stop` 通过状态目录上的文件锁串行执行。

M0 使用的状态与日志位置：

```text
~/.local/state/termux-desktop/session.json
~/.local/state/termux-desktop/desk.log
$PREFIX/tmp/termux-desktop-runtime   (XDG_RUNTIME_DIR, mode 0700)
```

`XDG_RUNTIME_DIR` 是 `$PREFIX/tmp/termux-desktop-runtime`，由 `desk start` 创建并校验：必须是当前 UID 所有的真实目录，不能是符号链接，权限为 0700。它仍在共享的 `$PREFIX/tmp` 下，X11 socket 和锁文件路径不变；`XDG_CONFIG_DIRS`、`XDG_DATA_DIRS` 等其他 XDG 目录保持默认。

## 4. M1：原生 Firefox

Firefox 装在 Termux 本体，不经过 proot。它和 XFCE 用同一个 X server 和同一个显示名，不需要容器，也不需要 `xhost`。

- `desk setup` 把 `firefox` 放进固定包列表，和桌面依赖一起安装；`desk start` 仍然不安装任何包。
- 会话把 `XDG_DATA_DIRS` 设为 `$PREFIX/share`，所以 Termux 包自带的 `firefox.desktop` 直接出现在 XFCE 菜单里，不需要手写菜单项。
- 如果菜单里没有，先确认 `$PREFIX/share/applications/firefox.desktop` 存在，再确认会话里的 `XDG_DATA_DIRS`。
- 会话把 `LANG` 和 `LC_ALL` 固定成 `C.UTF-8`。Android 的 libc 只提供 C 和 C.UTF-8；用别的 UTF-8 名字时 Xlib 会退回 ASCII，窗口标题和剪贴板里的非 ASCII 文本会坏掉。
- 已经装过的依赖可能比二进制需要的旧。包系统按名字判断依赖是否满足，不会因为 soname 变了去升级，`desk setup` 也不升级已安装的包。这种情况 `desk doctor` 会运行 `firefox --version`，把加载器报的缺库名字原样列出来。

## 5. M2：proot 应用接入

M2 只支持一个容器和一个手写菜单项，用于验证容器 GUI 能否显示。

```mermaid
flowchart LR
  M["XFCE 菜单项"] --> D["desk app launch container/app"]
  D --> P["proot-distro login CONTAINER"]
  P --> E["env DISPLAY=:0<br/>XDG_RUNTIME_DIR=..."]
  E --> A["容器内 GUI 应用"]
  A --> X["termux-x11 :0"]
```

启动命令：

```text
pd sh <container> --isolated \
  --bind /data/data/com.termux:/termux \
  --bind /data/data/com.termux/files/home:/home/termux \
  --bind /storage/emulated/0:/sdcard \
  --shared-tmp \
  -e DISPLAY=:0 -- <app>
```

`pd` 是 `proot-distro` 的别名，`sh` 是 `login` 的别名。

这条命令在新建的 `ubuntu-desktop-test` 容器上验证过，`xterm` 能显示在 XFCE 里。已确认的几点：

- 用 `--isolated`，是因为默认模式除了 tmp 和 X11 socket，还会隐式绑定宿主 home、Android 共享存储、`/system`、`/vendor`、`/odm`、`/product`、linker 配置和 Termux 应用的 cache 目录。isolated 模式只保留 `/dev`、`/proc`、`/sys` 和容器自己的 `shm`，其余挂载全部来自显式 `--bind`。
- 挂载点必须先在容器里存在。`proot-distro` 不创建 `--bind` 的目标目录。
- 容器不继承宿主的环境变量。`proot-distro` 自己构造环境：`PATH`、镜像清单里的 `Env`、容器标识变量、`-e` 传入的变量、`HOME`、`USER`、`TERM`。所以 `DISPLAY` 必须用 `-e` 传。
- 不需要 `xhost`，也不需要 `XAUTHORITY`。`termux-x11` 不校验 X11 认证。
- `proot` 和容器内进程不新建会话或进程组，跟随父进程。从桌面会话里启动的应用仍然属于会话进程组，`desk stop` 按进程组清理可以覆盖到。
- `/termux` 是权威路径，`/home/termux` 是便捷路径，两者指向同一份内容。容器 `HOME` 仍然是容器自己的 `/root`。
- 宿主 home 里指向宿主绝对路径的符号链接在容器内会悬空，例如 `~/termux-desktop`。
- 杀掉 `proot` 本身不会带走容器内进程。清理必须按进程组给所有成员发信号。

M2 验收应用：`xterm`。它是容器内的 X11 终端，能打开就说明从容器到 X server 的显示路径通了。

### 准备一个容器：`desk container init`

上面列出的准备步骤已经收进 `desk container init <容器名>`。它做四件事：容器不存在时用 `proot-distro install` 创建；读容器里的 `/etc/os-release` 判断发行版家族；创建三个挂载点；按家族安装 X 客户端和字体。最后在容器里检查挂载点和 `xterm` 都在。重复执行只做缺的步骤。

`desk container init` 不实现镜像下载、rootfs 解包和包管理逻辑，这些仍然由 `proot-distro` 和容器自己的包管理器负责。发行版家族写在 `src/termux_desk/container.py` 的 `DISTRO_FAMILIES` 和 `PROFILES` 里，现在只有 debian 和 ubuntu，Alpine 会明确报错并列出支持范围，不会静默跳过。

`desk doctor` 会检查 `proot-distro` 是否存在，缺失时只给警告，因为桌面会话不需要它。

### 启动一个应用：`desk app launch <容器>/<应用>`

它把上面那条命令固定下来：从会话状态里读显示名，按 `MOUNT_POINTS` 组装三个 `--bind`，带上 `--isolated`、`--shared-tmp` 和 `-e DISPLAY=<显示名>`，然后用 `exec` 替换掉自己。

- 没有会话状态，或者 `$PREFIX/tmp/.X11-unix/X0` 不存在时拒绝启动，提示先运行 `desk start`。
- 不 detach，也不新建会话或进程组。从桌面会话里启动的应用仍然属于会话进程组，`desk stop` 能清到它。
- `exec` 之后 `desk` 进程就是 `proot` 进程，应用的退出码就是 `desk app launch` 的退出码，中间不留一层。
- 应用名不能以 `-` 开头，避免被当成选项；容器名沿用 `container init` 的规则。
- 不传参数时，`<应用>` 同时就是程序名；传了 `--` 之后的参数时，参数就是完整命令行。生成的菜单项走后者，因为容器里 `.desktop` 的文件名和程序名不一定相同（例如 `debian-xterm.desktop` 里的程序是 `xterm`）。

手写菜单项放在宿主的 `~/.local/share/applications/` 里，`Exec` 直接写 `desk app launch <容器>/<应用>` 的绝对路径：`desk` 还没有安装到 `$PREFIX/bin`（安装位置见未决问题 Q-02），菜单项不能靠 PATH 找到它。

实测：从 XFCE 菜单启动的 xterm 和它的 `proot` 进程组都是会话进程组的 id，父进程是 `xfce4-panel`。`desk stop` 之后两者都没有残留，`doctor` 回到 13 ok、0 warning、0 failure。

一个裸的 Ubuntu 容器要跑 xterm，需要先准备：

```text
mkdir -p /termux /home/termux /sdcard
apt-get install -y --no-install-recommends xterm x11-utils xfonts-base fonts-dejavu-core
```

裸镜像没有 X 客户端也没有字体。`xdpyinfo` 只验证连接，`xterm` 才能验证字体和真实渲染。

## 6. M3：多容器与菜单聚合

M3 目标：应用安装在不同容器中，菜单统一展示。

```mermaid
flowchart TB
  Scan["desk app refresh"] --> C1["扫描 ubuntu-desktop-test<br/>/usr/share/applications"]
  Scan --> C2["扫描其他容器<br/>/usr/share/applications"]
  C1 --> Cat["扫描结果<br/>container + app-id"]
  C2 --> Cat
  Cat --> Menu["生成宿主 .desktop 菜单项"]
  Menu --> Launch["desk app launch container/app"]
  Launch --> Proot["proot-distro login"]
  Proot --> X["同一个 X server"]
```

应用标识建议：

```text
<container>/<app-id>
```

例如：

```text
ubuntu-desktop-test/xterm
debian-dev/gimp
```

菜单项需要带容器名，避免同名应用混淆。

`desk app refresh` 现在这样做：

- 只扫每个容器的 `/usr/share/applications`，不扫容器用户的 `~/.local/share/applications`。
- 跳过 `Type` 不是 `Application`、`Hidden` 或 `NoDisplay` 是 true、`Terminal` 是 true、没有 `Name` 或 `Exec` 的条目。带终端的应用跳过，因为 `desk app launch` 不会给它一个终端，菜单里点开也只会什么都不显示。
- `Exec` 按 POSIX shell 规则切分，去掉 `%f`、`%U` 这类字段代码；解析失败的条目跳过。
- 图标只用名称。`Icon` 是容器内绝对路径时忽略，因为那个路径在宿主上不存在。
- 每个条目写到 `~/.local/share/applications/desk-<容器>-<应用>.desktop`，`Name` 带 `(<容器>)` 后缀，并用 `X-Desk-Container` 和 `X-Desk-App` 记录来源。同名应用靠容器名区分。
- `Exec` 是 `desk app launch <容器>/<应用> -- <容器里的命令行>`，所以参数也一起传过去。
- 刷新会删掉不再匹配的 `desk-*.desktop`，手写的菜单项不受影响。

暂不做：合并重复应用、复制图标、扫描缓存。桌面环境什么时候看到新条目由它自己的菜单监视决定，`desk` 不强行刷新。

## 7. 文件系统与进程间通信

### home

- **不共享 home**。每个容器使用自己的 root home。
- 不使用 `--shared-home`，避免不同发行版的应用配置互相覆盖。
- 项目定义容器文件系统的主要挂载和路径约定：`/termux` 是 Termux 目录，作为权威路径；`/home/termux` 是宿主 home，作为敲命令时的便捷路径；`/sdcard` 是 Android 共享存储。
- 容器 `HOME` 仍然是容器自己的 `/root`，`/home/termux` 只是一个挂载点，不改变容器用户的 home。
- 用户可以在项目约定的挂载点之上绑定自定义目录。
- 容器 rootfs 本身隔离 home，不需要额外的“每容器桌面用户”来实现配置隔离。

### /tmp 和 X11

`--shared-tmp` 的语义：

```text
宿主 $PREFIX/tmp  ←→  容器 /tmp
```

`--shared-x11` 的语义：

```text
宿主 $PREFIX/tmp/.X11-unix  ←→  容器 /tmp/.X11-unix
```

`--shared-tmp` 包含 `--shared-x11`。proot-distro 默认模式下两者已经启用；`--isolated`/`--minimal` 才需要显式声明。

实测 `--shared-tmp` 展开为 `--bind=$PREFIX/tmp:/tmp`，共享的是整个 `$PREFIX/tmp`，不是只有 X11 socket 目录。`--isolated` 下不声明时，容器 `/tmp` 是容器自己的目录。

因为 `/termux` 已经挂了整棵 Termux 目录，`$PREFIX/tmp` 在容器里同时是 `/tmp` 和 `/termux/files/usr/tmp`。所以 `--shared-tmp` 和 `--shared-x11` 在“容器能否访问”上没有区别，区别只在临时文件是否互相污染。

共享 tmp 的影响：

- 好处：X11 socket 直接可见，后续音频/DBus 也容易接。
- 风险：多个容器共享同一个 `/tmp`，临时文件、socket、锁可能冲突。
- 风险：容器和宿主可以互相读写临时文件。
- 风险：残留的 `.X0-lock`、`.X11-unix/X0` 可能导致下次启动失败。
- 结论：M2 先用 `--shared-tmp`；M3 推荐改成 `--shared-x11` + 每容器独立 `/tmp` bind，但需要验证。

## 8. 身份与权限模型

本项目的工程假设：

- **不把 Android/Unix 用户权限当作本项目的隔离机制。**
- Android 仍有 app sandbox、SELinux 和存储限制；这些是平台约束，不是本项目可设计的权限层。
- proot 内的 `root` 是容器内的身份映射；落到宿主仍是 Termux 应用 UID，不是宿主 root。
- 容器内的文件写入最终仍受 Termux 应用 UID 和 Android 限制约束。
- 因此 proot 不是安全沙箱，容器之间也不是强安全边界。

基于这个模型：

- M2 起使用容器内 root 运行应用，使用每容器独立的 root home。
- 暂不创建“每容器专用桌面用户”；它不能提供真正的安全隔离，还会引入 UID 映射和文件权限复杂度。
- 如果后续某个应用拒绝 root，或需要独立的用户级配置，再按需引入专用用户。
- 不给“专用用户”赋予安全承诺；它只是应用行为和配置管理上的选择。

## 9. 进程、状态与清理

- 所有会话进程放在同一个进程组。
- `desk` 记录会话 PGID。从桌面会话里启动的应用仍在同一个进程组里，不需要单独登记。
- 容器里的 `proot` 和容器内进程也不新建进程组。杀掉 `proot` 本身不会带走容器内进程，信号必须发给进程组的全部成员。
- `desk stop` 先终止应用，再终止桌面和 X server。
- M0 只按进程组清理，不按命令名搜索并杀进程。
- 如果进程组清理后仍有残留，`desk stop` 报错并保留状态；这表示启动方式需要修，不能用 `pkill -f` 掩盖。
- 状态文件只记录可验证的事实：PID、PGID、display、启动时间、日志路径、boot id、会话标识、会话进程的启动时间。
- 日志是排障入口，不是状态来源。

## 10. 约束

- 只支持同 CPU 架构的 proot 容器；x86 应用需要 box64/qemu，当前不做。
- 只支持 X11/XWayland 应用；纯 Wayland 不在 MVP。
- 不做 VNC、音频、GPU、chroot。
- 不修改用户的 shell 启动文件。
- 所有运行和验证都在 Termux 本体完成；不要在另一个 proot 容器里嵌套验证桌面。

## 11. 参考项目

参考项目是“安装器 + 配置生成器 + 启动脚本集合”，不是桌面运行时。它覆盖 14 种 DE/WM、2 种 GUI 模式、2 种包管理器、2 种容器和多种 GPU 驱动，复杂度主要来自这些维度的组合。

我们只借用：

- `tx11start` 的启动顺序
- `TracerPid` 的 proot 防护思路
- `pdrun` 的容器环境包装思路
- 系统检查和故障排查项

详见 [参考项目笔记](reference.md) 和 [决策记录](decisions.md)。

## 12. 安装、更新与发布

### 安装布局

```text
$PREFIX/bin/desk                              命令入口
$PREFIX/lib/termux-desktop/src/termux_desk/   Python 包
$PREFIX/lib/termux-desktop/templates/         会话脚本模板
$PREFIX/lib/termux-desktop/VERSION            版本和来源
$PREFIX/lib/termux-desktop.previous/          上一次成功安装的副本
```

`$PREFIX/lib/termux-desktop` 保持和仓库相同的形状：`config.repository_root()` 取 `config.py` 的 `parents[2]`，安装后正好是 `$PREFIX/lib/termux-desktop`，会话脚本模板不用改代码就能找到。入口文件 `desk` 先找自己旁边的 `src`，再找 `../lib/termux-desktop/src`，仓库内和安装后共用同一个文件。

`VERSION` 有两行，`source` 区分副本的来源：

```text
version=0.0.1
source=checkout            # 从目录安装
source=release:v0.0.1      # 从发布归档安装
```

### 安装

`desk install --source <目录>` 的步骤：

1. 检查目录里有 `desk`、`src/termux_desk/cli.py` 和 `templates/session.sh`。
2. 检查 `$PREFIX/bin/desk` 是否由 desk 安装；不是的话要求加 `--force`，避免覆盖别的程序。
3. 在 `$PREFIX/lib/termux-desktop.new` 里铺好新副本，跳过 `__pycache__`。
4. 旧副本改名为 `termux-desktop.previous`，新副本改名为 `termux-desktop`，两次改名紧挨着。
5. 复制入口文件并设为可执行。

从发布归档安装时，`install.sh` 把 tag 传给 `desk install --release`，`VERSION` 的 `source` 写成 `release:<tag>`；不加 `--release` 时写成 `checkout`。复制和改名失败时旧副本仍在原位；更新不碰状态、日志和菜单条目。

### 更新

`desk update` 的步骤：

- 读 `VERSION`。来源是 `checkout` 时拒绝替换，除非加 `--force`。
- 没给 `--version` 时，请求 `https://github.com/<仓库>/releases/latest` 的重定向，`Location` 末尾就是最新 tag。用 HEAD 请求，不解析 GitHub API 的 JSON，也不占 API 限额。
- 下载 `archive/refs/tags/<tag>.tar.gz` 到临时目录，检查解压目录里的 `__version__` 与 tag 一致。
- 用和安装相同的替换步骤，`source` 写成 `release:<tag>`。

失败时上一份副本仍在。`desk update --check` 只打印已装版本和可装版本，不改任何文件。

### 归档安全

解压前逐个检查归档成员：路径必须落在解压目录内，不接收符号链接和硬链接。检查不过就报错，不留下部分解压的目录。

### 发布

- 版本号只写在 `src/termux_desk/__init__.py`。
- 打 `vX.Y.Z` tag，`git push --tags`。
- `gh release create vX.Y.Z --notes ...`。
- 发布归档由 GitHub 自动生成，不需要上传附件。
- 目前手动发布，不用 GitHub Actions；稳定后再考虑。
