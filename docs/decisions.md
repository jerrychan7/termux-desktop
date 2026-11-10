# 决策记录

记录已经对齐的决策，供后续实现和评审使用。
状态：`Accepted` 表示当前按此执行；`Proposed` 表示方向但未验证；`Open` 见 [未决问题](open-questions.md)。

日期：2026-11-10

## 索引

| ID | 决策 | 状态 |
|---|---|---|
| D-001 | 原生桌面，容器只提供应用 | Accepted |
| D-002 | 复用 Termux:X11，不自研 X server | Accepted |
| D-003 | M0 使用 XFCE | Accepted |
| D-004 | MVP 只用 Firefox | Accepted |
| D-005 | 不引入 DSH | Accepted |
| D-006 | 编辑器移出 MVP | Accepted |
| D-007 | Python 主逻辑，Bash 只做薄启动脚本 | Accepted |
| D-008 | 分阶段 M0-M3，M0 不碰 proot | Accepted |
| D-009 | 只支持同 CPU 架构容器 | Accepted |
| D-010 | MVP 只支持 X11，Wayland 后续 | Accepted |
| D-011 | 不共享 home，容器目录挂载由项目安排 | Accepted |
| D-012 | M2+ 使用容器 root，不建专用桌面用户 | Accepted |
| D-013 | M2 用 `--shared-tmp`，M3 再收紧 | Accepted |
| D-014 | 菜单先手写，自动扫描后置 | Accepted |
| D-015 | 应用安装由容器负责，`desk` 只发现和启动 | Accepted |
| D-016 | 进程组 + JSON 状态 + 日志，无常驻 daemon | Accepted |
| D-017 | 只参考参考项目，不复制代码 | Accepted |
| D-018 | 安装器 UI 后置，先 Rich/Questionary，后 Textual | Accepted |
| D-019 | M0 用独立的 `desk setup` 安装固定依赖 | Accepted |
| D-020 | M0 只按进程组清理，不用 `pkill -f` 兜底 | Accepted |
| D-021 | 用 `--isolated` 加显式挂载启动容器应用 | Accepted |
| D-022 | 容器由 `desk container init` 创建和准备 | Accepted |
| D-023 | `desk app launch` 前台 exec 启动容器应用 | Accepted |
| D-024 | 菜单条目由 `desk app refresh` 扫描生成 | Accepted |
| D-025 | `desk` 装在 `$PREFIX/bin` 和 `$PREFIX/lib/termux-desktop` | Accepted |
| D-026 | 发布产物是 GitHub 源码归档，手动 `gh release create` | Accepted |
| D-027 | `install.sh` 只下载解压，复制交给 `desk install` | Accepted |
| D-028 | 菜单图标从容器复制到宿主图标目录 | Accepted |
| D-029 | `desk pkg` 只转发参数给容器包管理器并刷新菜单 | Accepted |
| D-030 | `desk start` 启动前自动刷新菜单 | Accepted |

---

## D-030 `desk start` 启动前自动刷新菜单

**决策**

`desk start` 在启动会话前运行一次静默的菜单刷新。`proot-distro` 不存在时直接跳过；刷新失败时打印警告并继续启动会话。

**原因**

- 菜单不只通过 `desk pkg` 产生：用户可以在容器里直接跑 `apt install`，或者在桌面会话里装包。启动时刷新让任何安装路径最终都被同步。
- XFCE 在启动时读取菜单，放在启动前刷新比启动后刷新可靠。
- 刷新只读容器、只写用户目录，不安装包，不违反“`desk start` 不装依赖”的边界。

**后果**

- 每次启动多一次容器扫描；没有 `proot-distro` 就没有容器，直接跳过。
- 刷新失败不阻断会话；用户仍然可以手动运行 `desk app refresh`。

## D-029 `desk pkg` 只转发参数给容器包管理器并刷新菜单

**决策**

`desk pkg <容器> <参数>…` 先读 `/etc/os-release` 决定用哪个包管理器（debian 家族用 `apt`），把参数原样交给它，只把 `add` 换成 `install`、`rm` 换成 `remove`。执行结束之后无论成功失败都跑一次 `desk app refresh`，打印该容器现在的菜单项数，然后返回包管理器的退出码。容器名必填。

**原因**

- 包管理器的默认值、提示、输出和退出码都是它自己的；desk 再加 `-y` 或 `--no-install-recommends` 就是替用户做决定，连“要不要默认装推荐”的争论也不需要 desk 层面的开关。
- 用户装完软件最关心“菜单里有没有”，自动刷新把这件事变成默认结果。
- 容器名必填，宿主包继续用 `pkg`，不靠猜容器名来分流，避免静默装到错误的位置。

**后果**

- 动词不设白名单：`apt` 有什么子命令就能用什么，`search`、`show`、`policy`、`autoremove` 都直接可用；拼错时由 `apt` 报错。
- 不自动 `apt update`，不设置 `DEBIAN_FRONTEND`，不校验包名。
- 刷新失败只打印警告，退出码仍然是包管理器的。
- `desk container init` 仍然用 `apt-get` 和 `--no-install-recommends`，因为那是 desk 自己安装的基线，不是用户装包。

## D-028 菜单图标从容器复制到宿主图标目录

**决策**

`desk app refresh` 读条目里的 `Icon`：是名称时按固定目录顺序在容器里找 `.png`、`.svg`、`.xpm`，是绝对路径时直接读；找到的文件用 base64 传回宿主，写到 `~/.local/share/icons/termux-desktop/<容器>/`，条目里的 `Icon` 写成宿主绝对路径。

**原因**

- 宿主没有容器里的图标主题，`Icon=Alacritty` 这样的名字在宿主上解析不到，菜单只显示通用图标。
- 容器里的绝对路径在宿主上不存在，不能直接写进条目。
- 只传文件内容，不挂载容器目录，`app refresh` 不需要新的挂载点。

**后果**

- 图标文件由 `desk app refresh` 管理：刷新时删掉不再匹配的文件，容器被删时删掉整个目录。手写菜单项不受影响。
- 每个图标最多读 512 KiB；读不到、读失败或超限时条目保留原来的图标名，菜单退化成通用图标。
- `--shared-tmp` 时容器仍能访问宿主文件系统，但这条路径只读文件、不写，不改变隔离范围。

## D-025 `desk` 装在 `$PREFIX/bin` 和 `$PREFIX/lib/termux-desktop`

**决策**

`desk install` 把入口文件放到 `$PREFIX/bin/desk`，把 Python 包和会话脚本模板放到 `$PREFIX/lib/termux-desktop/{src,templates}`，把版本记录写到 `$PREFIX/lib/termux-desktop/VERSION`。不放进 Python 的 `site-packages`。

**原因**

- `$PREFIX/bin` 本来就在 Termux 的 PATH 上，安装后直接可以调用 `desk`，不需要改 shell 启动文件。
- 安装目录保持和仓库一样的形状（`src/`、`templates/`），`config.repository_root()` 取 `Path(__file__).parents[2]` 正好落在 `$PREFIX/lib/termux-desktop`，会话脚本模板不用改代码就能找到。
- 入口文件 `desk` 自己先找同目录的 `src`，再找 `../lib/termux-desktop/src`，所以仓库里和安装后是同一个入口文件。
- 不写 `site-packages`，避免和 Python 版本绑定，也避免和 pip 抢目录。

**后果**

- 菜单条目的 `Exec` 在安装后是 `$PREFIX/bin/desk`，不再是仓库路径；已有的条目要重新运行一次 `desk app refresh`。
- 入口文件的 shebang 固定为 `/data/data/com.termux/files/usr/bin/python3`，和仓库里的 `desk` 相同。
- 用户级安装（`~/.local/bin`）不在默认 PATH 上，因此不采用。
- `install.sh` 从发布归档安装时传 `--release <tag>`，`VERSION` 记录 `release:<tag>`；直接跑 `./install.sh` 时记录 `checkout`。

## D-026 发布产物是 GitHub 源码归档，手动 `gh release create`

**决策**

版本号只有一处：`src/termux_desk/__init__.py` 的 `__version__`。发布时打 `vX.Y.Z` 的 tag，并让 GitHub 自动生成源码归档（`archive/refs/tags/vX.Y.Z.tar.gz`）。发布由维护者手动执行，不用 GitHub Actions。

**原因**

- 源码归档不需要构建步骤，也不需要上传附件；tag 一旦打出，内容就固定了。
- Termux 基装自带 `curl` 和 `tar`，安装脚本不需要 git，也不需要额外安装包。
- 包没有第三方依赖，不需要构建产物，源码归档就是完整安装包。
- 目前的安装和更新还处在验证阶段，手动发布能看清每一步，等流程稳定再考虑自动化。

**后果**

- 归档包含整个仓库（`tests/`、`docs/`），但安装只复制 `src/` 和 `templates/`，多余文件没有影响。
- `gh` 的 token 目前没有 `workflow` scope，推 `.github/workflows` 会失败；这是选择手动发布的原因之一。

## D-027 `install.sh` 只下载解压，复制交给 `desk install`

**决策**

`install.sh` 在仓库里直接运行 `desk install --source .`；从网络安装时只做下载归档、解压、调用 `desk install --source <解压目录>` 三步。复制文件、写版本记录、替换旧副本都在 `desk install` 里。

**原因**

- 安装逻辑只有一份，并且是 Python，可以用单元测试覆盖；shell 脚本只负责找文件。
- 解压目录的形状和仓库一致，`desk install` 不需要区分来源。

**后果**

- `install.sh` 需要 `curl`、`tar`、`python3`；缺 `python3` 时脚本先用 `pkg install` 安装它。
- 解压后的目录名不确定（`termux-desktop-0.0.1`），脚本用一个循环找到含 `desk` 的那一层。
- 下载安装时脚本把 tag 传给 `desk install --release`，这样安装来源记录成发布版本，`desk update` 不会把它当成仓库副本拒绝。

## D-001 原生桌面，容器只提供应用

**决策**

桌面、XFCE、Firefox 和会话管理器都运行在 Termux 本体。proot 容器只用来安装和运行应用；所有 GUI 应用连同一个 X server。

**原因**

- 避免整个桌面经过 proot 的 syscall 开销。
- 保留 Termux 原生的性能和 Android 集成。
- 应用兼容性和桌面性能解耦。

**后果**

- 需要单独解决容器应用的 X11 接入。
- 容器不能提供完整桌面体验；这是有意的限制。

## D-002 复用 Termux:X11，不自研 X server

**决策**

使用 Termux:X11 作为显示层。优先 sharedUid 构建，减少切到 X11 后 Termux 被 Android 降频或限制。

**原因**

- 自研 X server/Android 显示 App 的成本远高于本项目目标。
- Termux:X11 已经解决显示、输入和 Android Activity 集成。

**后果**

- 依赖 Termux:X11 App 和 `termux-x11` 包。
- sharedUid 构建要求签名匹配；装不上时回退普通构建。

## D-003 M0 使用 XFCE

**决策**

第一阶段只支持 XFCE。

**原因**

- Termux 上资料最多、组件完整、适合作为基线。
- 先验证会话能启动、运行和退出，不引入对多种 WM 的支持。
- 如果后续确认键鼠为主，再评估 i3 等平铺 WM。

**后果**

- 触屏优先的默认体验以 XFCE 为准。
- 多 DE/WM 不在 MVP。

## D-004 MVP 只用 Firefox

**决策**

浏览器只保留 Termux 原生 Firefox；不做 Chromium、Google Chrome 或 DSH Web UI。

**原因**

- 减少需要支持的浏览器数量。
- Firefox 在 Termux/X11 上相对稳定。
- Chrome 专属测试当前不是需求。

**后果**

- 以后需要 Chrome 兼容测试时再单独评估。
- 不在容器里安装第二套浏览器。

## D-005 不引入 DSH

**决策**

DSH 完全移出项目范围：不作为桌面应用、不作为浏览器标签、不作为容器依赖。

**原因**

- 目标是通用桌面环境，不是围绕 DSH 的壳。
- 去掉 DSH 后，会话、文件和容器模型更独立。

**后果**

- 不依赖 `127.0.0.1:3080`。
- DSH 以后可以作为一个普通浏览器标签存在，但不进入架构。

## D-006 编辑器移出 MVP

**决策**

VS Code、code-oss、code-server 和 Neovim 都不在第一阶段范围内。

**原因**

- 先验证桌面和容器 GUI 能否正常显示。
- 编辑器选型会引入扩展兼容性、资源占用和容器/原生选择问题。

**后果**

- 后续加编辑器时，`desk app` 模型不需要改变。
- 当前不承诺原生或容器方案。

## D-007 Python 主逻辑，Bash 只做薄启动脚本

**决策**

会话管理器、检查、配置和状态逻辑用 Python 3；Bash 只作为生成出来的 `session.sh`，负责环境变量、启动 X server 和退出清理。

**原因**

- Python 适合编排进程、解析配置、写状态和测试。
- Bash 适合最后的环境变量、启动和退出清理，但不适合承载大项目逻辑。
- 参考项目的单体 Bash 结构不适合复制。

**后果**

- 需要 Termux 本体的 Python 运行时。
- 生成的 `session.sh` 保持可读、可手工调试。
- `session.sh` 不使用 `exec`，这样才能保留退出 trap 去清理 X server。

## D-008 分阶段 M0-M3，M0 不碰 proot

**决策**

- M0：安装固定依赖 + XFCE 会话启停
- M1：Firefox
- M2：单容器 + 手写 xterm 菜单
- M3：多容器 + 自动扫描

**原因**

- 先消除桌面显示和进程启停的风险。
- 容器 GUI 显示是独立风险，不应和 M0 混在一起。

**后果**

- 第一版没有 proot、没有 Firefox、没有容器。
- 每个阶段都有独立验收标准。

## D-009 只支持同 CPU 架构容器

**决策**

只支持与设备同架构的 proot 容器。

**原因**

- proot 不做 CPU 指令翻译。
- x86 应用需要 box64/qemu，属于另一条技术路线。

**后果**

- “任意容器”指同架构的不同发行版，不是任意架构。
- x86 支持不在当前路线图内。

## D-010 MVP 只支持 X11，Wayland 后续

**决策**

MVP 只接入 X11/XWayland 应用；纯 Wayland 不在当前范围。

**原因**

- Termux:X11 是 X11 方案。
- Wayland 需要另一套显示/合成器架构。

**后果**

- 纯 Wayland 应用暂不可用。
- Wayland 支持记录在路线图，不在 MVP。

## D-011 不共享 home，容器目录挂载由项目安排

**决策**

不使用 `--shared-home`。每个容器使用自己的 home；容器里哪些目录挂载到哪里、路径如何约定，由项目统一安排；用户可以在这些约定之上绑定自定义目录。

**原因**

- 共享 home 会让不同发行版的应用配置互相覆盖。
- 每容器 rootfs 已经提供了 home 隔离。
- 容器目录如何挂载应由项目统一安排，而不是预设一个全局共享目录。

**后果**

- 默认不预设共享目录；用户自定义目录需要显式 bind。
- 容器之间的应用配置天然分离。

## D-012 M2+ 使用容器 root，不建专用桌面用户

**决策**

M2 起在容器内以 root 身份运行应用，使用每容器独立的 root home；暂不创建“每容器专用桌面用户”。

**原因**

- 本项目不把 Android/Unix 权限当作隔离边界。
- Android 仍有 app sandbox、SELinux 和存储限制；proot 内的 root 只是容器内身份映射，不是宿主 root。
- 专用用户不能把 proot 变成安全沙箱，却会引入 UID 映射、文件属主、X11/GPU 权限等复杂度。
- 每容器 root home 已经解决配置隔离问题。

**后果**

- 某些拒绝 root 的应用可能需要额外参数或后续引入用户。
- 专用用户作为条件触发的后续项，不作为 MVP 前置条件。

## D-013 M2 用 `--shared-tmp`，M3 再收紧

**决策**

M2 使用 `--shared-tmp` 打通 X11；M3 多容器阶段再评估 `--shared-x11` + 每容器独立 `/tmp`。

**原因**

- `--shared-tmp` 最简单，和参考项目的验证路径一致。
- 共享整个 `/tmp` 会带来跨容器冲突和临时文件泄漏。

**后果**

- M2 接受较宽的 `/tmp` 共享。
- 实测 `--shared-tmp` 等价于 `--bind=$PREFIX/tmp:/tmp`，共享的是整个 `$PREFIX/tmp`。
- M3 需要验证更窄的 bind 方案；在验证前不把共享 tmp 当成长期安全方案。

## D-014 菜单先手写，自动扫描后置

**决策**

M2 手写一个 xterm 菜单项；M3 再做多容器 `.desktop` 扫描和菜单聚合。

**原因**

- 先把“容器 GUI 能否显示”单独验证。
- 自动扫描涉及 Exec 解析、图标、去重和刷新，复杂度高。

**后果**

- M2 的菜单不通用。
- M3 才具备“应用装在不同容器、统一展示”的完整体验。

## D-015 应用安装由容器负责，`desk` 只发现和启动

**决策**

`desk` 不重新实现 apt/pacman。应用由各容器自己的包管理器安装；`desk` 负责发现、生成菜单和启动。

**原因**

- 包管理逻辑与发行版强绑定。
- 参考项目同时支持多个包管理器，这是它的主要复杂度来源之一。

**后果**

- M2/M3 需要容器内手动安装应用。
- `desk pkg` 后来补上了这层包装，只做参数转发和菜单刷新（见 D-029）。

## D-016 进程组 + JSON 状态 + 日志，无常驻 daemon

**决策**

会话通过进程组管理，状态写 JSON，日志写文件；`desk start` 启动后退出，不做常驻 supervisor。

**原因**

- 第一版需要可预测的启停和清理。
- 常驻 daemon 会引入 Android 后台限制、重启和状态同步问题。

**后果**

- 不支持自动拉起崩溃的会话。
- `desk status` 只报告事实，不猜测。
- 状态记录 boot id、会话标识和会话进程的启动时间；进程组对不上时不发送信号，并保留状态文件。
- `start` 和 `stop` 通过状态目录上的文件锁串行执行。

## D-017 只参考参考项目，不复制代码

**决策**

参考 `sabamdarif/termux-desktop` 的功能、流程和排障思路，不复制其代码。

**原因**

- 参考项目是 GPLv3，直接复制会影响本项目的许可证选择。
- 参考项目的单体 Bash 和所有功能组合不适合本项目。

**后果**

- 需要重新实现启动链和检查逻辑。
- 如果未来复制片段，先重新评估许可证。

## D-018 安装器 UI 后置

**决策**

先做核心 CLI；安装器 UI 后置。TUI 先用 Rich + Questionary，复杂后再评估 Textual；不做 GUI 安装器。

**原因**

- 安装时桌面可能还不可用，GUI 安装器故障面更大。
- 核心逻辑和 UI 分离，才能同时支持 TUI、纯 CLI 和测试。

**后果**

- MVP 没有图形安装器。
- 安装体验在核心稳定后再投入。

## D-019 M0 用独立的 `desk setup` 安装固定依赖

**决策**

M0 增加独立的 `desk setup` 命令，只安装固定的 Termux 包：

```text
x11-repo
termux-x11-nightly
xfce4
dbus
termux-am
```

`desk setup` 只安装缺失的包，不执行 `pkg upgrade`；先安装 `x11-repo`，刷新包索引，再安装其余包；不安装 Termux:X11 Android App；不安装 Firefox、proot、GPU 驱动或主题。`desk start` 永远不安装依赖，缺依赖时直接报错。

**原因**

- M0 的依赖是固定且有限的，适合现在在干净环境里一起验证。
- 安装失败和桌面启动失败是两类问题，分开更容易排障。
- 把安装限制在固定列表，避免一开始就长成完整的安装器。
- `start` 不应在启动桌面时修改系统包。

**后果**

- 首次使用需要先运行 `desk setup`。
- Termux:X11 App 仍需用户手动安装 APK，`doctor` 负责检查和提示下载地址。
- M1 加 Firefox 时，再扩展安装列表，不改变 `setup` 与 `start` 的职责划分。

## D-020 M0 只按进程组清理，不用 `pkill -f` 兜底

**决策**

M0 的 `desk stop` 只按会话进程组发送信号，不按命令名全局搜索并杀进程。清理后仍有残留时，`desk stop` 报错并保留状态，不使用 `pkill -f`。

**原因**

- 进程组能精确对应一次会话，不会误杀其他 X11 或桌面进程。
- `pkill -f` 依赖命令行文字，可能误杀、漏杀，或把清理不干净的问题藏起来。
- M0 正是验证进程组设计是否成立的阶段；如果失败，应该修启动方式。

**后果**

- 如果某个子进程自行脱离进程组，M0 会暴露问题，而不是自动掩盖。
- 后续如果需要强制清理，应作为显式的 `--force` 行为单独设计，不放进默认 `stop`。

## D-021 用 `--isolated` 加显式挂载启动容器应用

**决策**

M2 启动容器应用时使用 `--isolated`，并显式声明三个挂载：宿主 Termux 目录到 `/termux`、宿主 home 到 `/home/termux`、Android 共享存储到 `/sdcard`；X11 socket 通过 `--shared-tmp` 共享，显示变量用 `-e DISPLAY=:0` 传入。不使用 `--shared-home`，也不需要 `xhost`。

**原因**

- `proot-distro` 默认模式除了 tmp 和 X11 socket，还会隐式绑定宿主 home、Android 共享存储、`/system`、`/vendor`、`/odm`、`/product`、linker 配置和 Termux 应用的 cache 目录。挂载范围应当由项目写明，而不是由默认值决定。
- 在 `ubuntu-desktop-test` 上实测，`--isolated` 加上述三个挂载时，容器里的 `xterm` 能显示在 XFCE 上，不需要 `XAUTHORITY` 或 `xhost`。
- 容器的 home 仍然是容器自己的 `/root`，宿主 home 只是挂载在 `/home/termux`，不构成共享 home。

**后果**

- 挂载点必须先在容器里创建，`proot-distro` 不会创建 `--bind` 的目标目录。
- `/termux` 挂的是整棵 Termux 目录，其中包含所有容器的 rootfs，任意容器都能读写其他容器的文件。项目不把 proot 当安全边界，这个暴露是有意接受的；映射出去的路径只用于需要持久化的内容。
- 宿主 home 里指向宿主绝对路径的符号链接在容器内会悬空。
- `--shared-tmp` 共享整个 `$PREFIX/tmp`，不是只共享 X11 socket。
- 容器不继承宿主环境变量，`DISPLAY` 必须用 `-e` 传。

## D-022 容器由 `desk container init` 创建和准备

**决策**

`desk container init <容器名>` 负责建立一个新的 proot 容器的初始状态：容器不存在时调用 `proot-distro install`；读容器内 `/etc/os-release` 判断发行版家族；创建 `/termux`、`/home/termux`、`/sdcard` 三个挂载点；按家族安装 X 客户端和字体；最后在容器里检查挂载点和 `xterm`。重复执行只做缺少的步骤。

**原因**

- 挂载点必须先于挂载存在，手工执行时最容易漏，失败信息也最难懂。
- 一个裸镜像没有 X 客户端也没有字体。`desk` 需要知道用户容器至少装什么。
- 这些步骤只和发行版家族有关，和具体应用无关，适合在创建容器时一次做完。

**后果**

- `desk` 不重新实现镜像下载、rootfs 解包和包管理：创建调用 `proot-distro`，安装调用容器自己的包管理器。
- 现在只支持 debian 和 ubuntu。其它发行版在检测阶段报错并列出支持范围，不会跳过安装继续。
- 挂载点列表在 `container.py` 里只有一份，`desk app launch` 之后要复用它。
- `desk doctor` 增加 `proot-distro` 检查，缺失时只给警告，因为桌面会话不需要它。
- `desk container` 会写 `desk.log`，和 `setup`、`start`、`stop` 一样。

## D-023 `desk app launch` 前台 exec 启动容器应用

**决策**

`desk app launch <容器>/<应用>` 把容器的 GUI 应用启动到桌面上：从会话状态读显示名，用项目固定的三个挂载点，加 `--isolated`、`--shared-tmp` 和 `-e DISPLAY=<显示名>`，最后用 `exec` 替换自身进程。

**原因**

- 启动参数集中在一处。菜单项只需要调用这一条命令，以后改挂载或参数不用改每个菜单项。
- 前台 exec 保持进程组不变，`desk stop` 按进程组清理时能覆盖到容器应用。如果为了菜单不阻塞而 detach，应用会离开会话进程组，清理就会漏。
- `exec` 不留中间进程，信号和退出码直接对应应用本身。

**后果**

- 没有运行中的会话时拒绝启动。
- 容器名和应用名在启动前校验，应用名不能以 `-` 开头。
- 现在只接受一个程序名，不支持给应用传参数；需要参数时另做设计。
- 宿主共享存储用 `EXTERNAL_STORAGE` 的值，没有时退回 `/storage/emulated/0`；两者在同一台设备上通常指向同一份存储。
- 应用如果自己 fork 后让父进程退出，仍可能离开进程组；这种情况 `desk stop` 会报残留，而不是隐瞒。
- 已在设备上验证：从菜单启动的 `proot` 和 xterm 都落在会话进程组里，`desk stop` 清理后没有残留。

## D-024 菜单条目由 `desk app refresh` 扫描生成

**决策**

`desk app refresh` 扫描每个容器的 `/usr/share/applications`，为可用的条目在 `~/.local/share/applications` 写一个 `desk-<容器>-<应用>.desktop`。条目里的 `Exec` 调用 `desk app launch <容器>/<应用> -- <容器内命令行>`，`Name` 带容器名后缀。刷新时删除不再匹配的 `desk-*.desktop`。

**原因**

- 菜单条目必须带上容器标识，同名应用才能区分，而手写条目做不到这一点。
- 所有条目都走 `desk app launch`，启动参数集中在一处，改挂载或参数不用改每个条目。
- 固定前缀让刷新只清理自己写的文件，不会碰用户手写的菜单项。

**后果**

- 跳过 `Type` 不是 `Application`、`Hidden`、`NoDisplay`、`Terminal=true`、缺 `Name` 或 `Exec` 的条目。
- 图标只用名称，容器内的绝对路径图标被忽略。
- 只扫系统级目录，容器用户自己的 `.desktop` 不扫。
- 桌面环境是否立即显示新条目由它自己的菜单监视决定，`desk` 不强制刷新。
- 已在设备上验证：两个容器的应用同时出现在菜单里并能打开；同名应用带容器名后缀区分；卸载应用后刷新会删除对应条目。
