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
| D-031 | 许可证用 MIT | Accepted |
| D-032 | 容器应用启动时绑定系统字体并传入 UTF-8 locale | Accepted |
| D-033 | `desk setup` 按设备探测写入简体中文字体偏好文件 | Accepted |
| D-034 | `desk upgrade` 只升级 X11 仓库的已安装包 | Accepted |
| D-035 | 容器应用启动时绑定宿主的字体偏好文件 | Accepted |
| D-036 | 字体偏好按设备语言生成，并为其他 CJK 语言写 lang 规则 | Accepted |
| D-037 | 阶段二界面本地化本轮暂缓，实验 shim 冻结入库 | Accepted |
| D-038 | `desk app refresh` 检查 `Exec` 命令能否在容器中解析 | Accepted |
| D-039 | Firefox 从默认安装范围移除 | Accepted |
| D-040 | VNC 接入选用 TigerVNC，支持独立会话与镜像两种模式 | Accepted |
| D-041 | 会话以 display 编号为键，可并存 | Accepted |
| D-042 | 浏览器访问 VNC：noVNC + WebSocket 代理，默认 websockify_rs，Python 备选 | Accepted |
| D-043 | 本地回环 VNC 会话关闭 TigerVNC 黑名单 | Accepted |
| D-044 | VNC 镜像 :0 用 x0vncserver，display 与 --mirror 显式选择 | Accepted |
| D-045 | `desk stop` 收敛整个 display 的 web 代理、镜像与会话 | Accepted |
| D-046 | 父域命令对子域做编排与聚合：只读/清理聚合，暴露显式 | Accepted |
| D-047 | doctor 默认聚合 VNC 检查，--vnc 只跑 VNC | Accepted |
| D-048 | desk version 默认列出 VNC 组件，--vnc 只列 VNC | Accepted |
| D-049 | 容器域增加只读聚合 list/status，删除保持显式 | Accepted |
| D-050 | desk app doctor 只读检查容器应用的 Exec | Accepted |
| D-051 | desk font status 只读报告语言与字体家族 | Accepted |

---

## D-036 字体偏好按设备语言生成，并为其他 CJK 语言写 lang 规则

**决策**

- `desk setup` 从 Android 系统语言选择基础语言：`getprop persist.sys.locale` → `ro.product.locale` → `$LC_ALL`/`$LANG`；标签归一为 `zh-Hans`、`zh-Hant`、`zh-Hant-HK`、`ja`、`ko`，其他值（如 `en`）回退 `zh-Hans`，完全没有信息时也用 `zh-Hans`。
- 文件从 `63-cjk-prefer-sc.conf` 改名为 `63-cjk-prefer.conf`；`desk setup` 写新文件后删除带生成标记的旧文件。
- 基础语言的面写进 `sans-serif`、`serif`、`monospace` 别名；其他的语言如果探测到面，各写一组 `<match>` 规则（`test family eq` + `test lang contains` + `edit family prepend strong`）。规则顺序为 `zh` → `zh-hant` → `zh-hant-hk` → `zh-tw` → `zh-hk` → `ja` → `ko`，更具体的语言排在后面，覆盖宽泛规则。
- 某语言缺面时不写该语言的规则；基础语言缺面时整个文件不写，只警告。

**原因**

- 旧的 SC 方案对 ja/ko/TC/HK 用户都返回简体字形（实测 `lang=ja`、`lang=ko`、`lang=zh-tw`、`lang=zh-hk` 都落到 `Noto Sans CJK SC`），因为别名是无条件的，而 Noto CJK 各区域面的字符覆盖相同，`lang` 匹配不会把 SC 排除。
- 会话固定 `C.UTF-8`，宿主只有 `C` 和 `C.UTF-8`，不能依赖应用把正确的 `lang` 传给 fontconfig；设备系统语言是唯一可靠来源。
- `lang` 规则只在应用声明文本语言时生效：声明 `ja` 的应用拿 JP 面，不声明语言、但含中文文本的查询仍落到基础语言的面。

**后果**

- ja/ko/TC/HK 设备分别得到 JP/KR/TC/HK 基础面；其他区域语言仍可按内容语言拿到对应面。
- 非 CJK 设备（如 `en`）没有语言信息时回退简体中文面，不额外探测字符集。
- 已运行的应用要重启；容器仍由 `desk app launch` 绑定这份文件继承。
- 规则只覆盖通用家族 `sans-serif`/`serif`/`monospace`；应用请求具名字体时不受影响。

**验证**

- 设备（`persist.sys.locale=zh-Hans-CN`）生成 SC 基础别名和 18 条规则；ctypes `FcFontMatch` 实测 `lang=ja` → `Noto Sans CJK JP`、`lang=ko` → `Noto Sans CJK KR`、`lang=zh-tw` → `Noto Sans CJK TC`、`lang=zh-hk` → `Noto Sans CJK HK`、`charset=4E2D` → `Noto Sans CJK SC`。
- 容器同时绑定 `/system/fonts` 和这份文件后，`fc-match` 得到相同结果（`serif:lang=ja` → `Noto Serif CJK JP`）。

## D-037 阶段二界面本地化本轮暂缓，实验 shim 冻结入库

**决策**

- 本轮不再继续投入界面本地化，界面文字保持英文；多语言项从路线图的“桌面与显示”移到“暂缓”。
- 不把 preload shim 的菜单硬编码覆盖表、`.mo` 打包进产品或安装流程。
- 把实验 shim 源码以冻结状态入库 `scripts/frozen/l10n-shim/`，标记为“冻结、未启用”，作为后续重新投入的起点。

**原因**

- 边际收益递减：翻译数据可得，但 Termux 的 NDK 把 gettext 家族编译成返回 msgid 的内联函数，GLib/XFCE 按此构建；preload 只能翻译走了 `g_dgettext`/`g_dpgettext2` 符号的字符串，菜单栏与右键菜单走内联 `gettext()`，hook 不到。
- 要补全得靠 issue 214 那样的菜单硬编码覆盖表，绑版本、长期维护成本高；当前投入产出比低，决定先停。
- 收口原因不是“等上游修复”，而是主动暂缓；后续得空再评估，届时可以自行编译 XFCE/X11 相关组件从根上解决。

**后果**

- 宿主 XFCE 和容器应用的界面保持英文；阶段一的显示与字体行为不受影响。
- 菜单文件仍保留 `Name[locale]`，但界面语言不会因此切到中文。
- 重新投入时的起点是 `scripts/frozen/l10n-shim/`（shim 源码与说明）；如果届时 gettext 已可用或选择自行编译，再重新设计。
- `docs/i18n.md` 的阶段二前置调研保留过程记录，结论记在“阶段二验证结论”。

**验证**

- 证据：`$PREFIX/tmp/ph2`（`desk-l10n.so`、日志、`thunar.mo`、截图）与 `/root/shots/thunar-*.png`；测试全程未安装包。

## D-038 `desk app refresh` 检查 `Exec` 命令能否在容器中解析

**决策**

刷新菜单时，对每个容器收集所有条目的 `Exec` 首个命令，用一个 `sh` 脚本批量检查：绝对路径用 `[ -x ]`，裸名用 `command -v`。检查结果只用于警告：找不到命令的条目仍然写入菜单，并在刷新输出里逐条说明；检查本身失败（容器不可用、脚本报错）时静默跳过，不误报。启动应用后的进程级检查留待后续。

**原因**

- 容器里的包安装、升级、卸载会重写 `.desktop`，菜单可能指向已经不在的命令；VS Code 的测试里就出现过菜单项先于安装完成出现。启动时才失败对用户不友好。
- 先做解析检查，成本低、不依赖启动应用，也不会像真实启动那样留下进程或副作用；进程能否起来需要额外的超时与清理策略，放到后面。
- 不做逐应用硬编码：只认 `Exec` 的第一个词，路径检查与命名检查分开，适用于任何容器里的任何应用。

**后果**

- 刷新输出会多出 `the command ... was not found in the container` 警告和一条汇总；菜单条目不因此被过滤。
- 检查按容器各跑一次，命令集合去重后传给容器；容器不存在或检查失败时没有警告，与刷新失败的处理一致。
- 只检查命令是否存在，不判断程序能否运行、参数是否正确。

**验证**

- 单元测试覆盖脚本生成、输出解析、警告输出与检查失败分支；全量 541 个测试通过。
- 设备实测：在 `ubuntu-desktop-test` 放一个 `Exec=/nonexistent/probe` 的条目后刷新，输出 `ubuntu-desktop-test/desk-check-probe: the command /nonexistent/probe was not found in the container` 和 `Found 1 application whose command was not found; it may fail to start.`；删除条目再刷新，对应菜单项被移除。

## D-035 容器应用启动时绑定宿主的字体偏好文件

**决策**

`desk app launch` 在宿主的 `$PREFIX/etc/fonts/conf.d/63-cjk-prefer.conf` 存在时，把它绑定到容器的 `/etc/fonts/conf.d/63-cjk-prefer.conf`。文件不存在时不加这条绑定；不经过 `desk app launch` 的容器进程拿不到它。

**原因**

- 宿主已经由 D-033/D-036 按语言把 CJK 回退改成对应区域面，但容器的 fontconfig 读自己的 `/etc/fonts/conf.d`；实测容器里 `sans-serif:charset=4E2D` 和 `lang=zh-cn` 都落到 `Noto Sans CJK JP`。
- 容器的 conf.d 编号和宿主一致（`60-latin`、`65-nonlatin`），宿主生成的 63 文件在容器里落在同一个位置，不需要为容器单独维护一份。
- 绑定单个文件到不存在的目标路径已验证可行，proot 在启动时补上挂载点，退出后消失；容器写入仍然不成立，宿主文件只读。
- 相比在 `desk container init` 时往容器 rootfs 写一份，绑定不会让容器里的配置和宿主字体变化脱节，也不用重复生成。

**后果**

- 通过 `desk app launch` 启动的容器应用，中文回退和宿主一致（实测容器 `fc-match` 全部变成 SC）。
- 依赖容器的 fontconfig 配置目录是 `/etc/fonts/conf.d`；主流发行版都是，换发行版时要确认。
- 绑定只在启动时发生，已经运行的容器应用不会重新读配置。
- 宿主偏好文件被删除后，新的启动不再绑定，容器恢复原来的选择顺序。

## D-034 desk upgrade 只升级 X11 仓库的已安装包

**决策**

新增 `desk upgrade`：先 `pkg update` 刷新索引，再用 `apt-get -s upgrade` 模拟，挑出候选版本 origin 为 `termux-x11` 的已安装包，交给 `pkg install -y` 升级；升级后再模拟一次，确认没有残留。`desk setup` 仍然只安装缺失的包，不升级。

**原因**

- X11 仓库重建包后设备上的旧版本不会跟着更新；`libvte` 的旧构建要 `libicuuc.so.78`，而 `libicu` 升到 79 后只剩 `.so.79`，`xfce4-terminal` 启动时报缺库。
- 把升级塞进 `desk setup` 会破坏“只装缺包、不升级”的约定，而且只覆盖一个包；单独命令能随时刷新整个 X11 仓库的已安装包。
- 只升级 X11 仓库的包，避免顺带升级 `termux-main` 里与桌面无关的包；apt 在满足依赖时仍可升级必要的 `termux-main` 包。

**后果**

- 用户可以主动执行 `desk upgrade` 修复仓库重建造成的缺库；`desk setup` 的行为不变。
- 依赖 `apt-get -s upgrade` 的输出格式和 `termux-x11` 这个 origin 名；仓库改名或 apt 改输出格式时需要跟着改。
- 被 apt 标记为 kept back 的包不会出现在模拟的 `Inst` 行里，`desk upgrade` 不会升级它们。

## D-033 desk setup 按设备探测写入简体中文字体偏好文件

（D-036 已把这里的简体中文限定扩展为按设备语言生成，文件名也改为 `63-cjk-prefer.conf`。）

**决策**

`desk setup` 在固定包安装完成后，探测宿主上实际存在的简体中文字体家族，按结果生成 `$PREFIX/etc/fonts/conf.d/63-cjk-prefer-sc.conf`：

- 探测通过 `$PREFIX/lib/libfontconfig.so` 的 `FcFontMatch` 完成，不需要安装 `fontconfig-utils`；
- 候选家族按顺序检查：`Noto Sans CJK SC`、`OPPO Sans 4.0 SC`、`Source Han Sans SC` 等，Noto 优先；
- 文件编号 63 让它排在 `60-latin` 之后、`65-nonlatin` 之前；
- 别名只写 `sans-serif`、`serif`、`monospace`；没有 Mono CJK 面时 monospace 回退到 sans 面；
- 没有简体面、fontconfig 不可用或目录缺失时只打印警告，setup 不失败。

文件带生成标记。同名文件存在但内容不是 desk 写的时候保持原样。`desk font remove` 删除带标记的文件，`desk uninstall --purge` 调用同一个删除函数。

**原因**

- 宿主 fontconfig 只点名韩文面（`Noto Sans CJK KR`、`Noto Sans Mono CJK KR` 等），应用请求通用家族时中文字符会回退到 KR 或 JP 面；实测 `sans-serif:charset=4E2D` 落到 `Noto Sans CJK KR`。
- 设备可能没有 Noto 面或换成了厂商面（本机还有 `OPPO Sans 4.0 SC`），家族名要探测而不是硬编码。
- 放在 `60-latin` 之后能保住拉丁文本的 DejaVu；放在 `65-nonlatin` 之前才能让中文优先到 SC 而不是 KR。
- 写在系统目录而不是 `~/.config/fontconfig/fonts.conf`：用户配置在 `50-user.conf` 加载，会早于 DejaVu，导致拉丁文本也改用 Noto。

**后果**

- 宿主 GTK 应用（Thunar、面板、菜单）的中文回退改用简体面；Firefox 有自己的按语言默认字体，可能看不出变化。
- 已经运行的应用需要重启才会重新读配置；fontconfig 配置只在进程启动时加载。
- 容器里的 fontconfig 是另一套，这个文件不影响容器；容器里如需同样偏好要单独写。
- 探测依赖 `libfontconfig.so` 存在（Termux 的 fontconfig 包提供）；设备上不需要安装 `fontconfig-utils`。
- 同名文件不带生成标记时，setup 不覆盖、`desk font remove` 不删除。

## D-032 容器应用启动时绑定 Android 系统字体并传入 UTF-8 locale

**决策**

`desk app launch` 在宿主存在 `/system/fonts` 时增加一个绑定：

```text
--bind /system/fonts:/usr/share/fonts/truetype/android
```

并固定传入 `LANG=C.UTF-8`、`LC_ALL=C.UTF-8`，和桌面会话使用的 locale 相同。容器仍然可以装自己的字体，绑定目录只是额外的一份。

**原因**

- 实测宿主能显示中文靠的是 `/system/fonts` 里的 Noto CJK 加 `C.UTF-8`；容器两样都没有，`fc-list :lang=zh` 为 0，`desk app launch` 也没有把 locale 传进去，xterm 的标题和正文按非 UTF-8 解释。
- 绑定在 proot 启动时建立、退出后消失，目标目录不需要预先存在；`/system` 是只读挂载，容器写入返回 `Read-only file system`，fontconfig 缓存落在容器自己的 `/var/cache/fontconfig`。
- 相比每个容器单独装字体，绑定不需要联网、不需要每容器重复安装，也不会让容器里的字体版本偏离设备。

**后果**

- 容器的字体渲染开始依赖 Android 的字体文件；换设备或系统字体变化时，容器里的字形跟着变。
- 宿主的 fontconfig 默认把中文回退到 Noto CJK 的 KR/JP 面；`desk setup` 现在会写入字体偏好文件修正宿主侧（见 D-033），容器里的 fontconfig 仍是原来的选择顺序。
- 绕过 `desk app launch`、直接 `proot-distro login` 启动的应用不会得到绑定和 locale。
- xterm 这类自己不做逐字回退的终端仍会显示方块；GTK 应用（Pango）不受影响。
- `LANG`/`LC_ALL` 固定为 `C.UTF-8`，和会话保持一致；阶段二如果要切换语言，这里要一起设计。

## D-031 许可证用 MIT

**决策**

项目采用 MIT 许可证，版权行写 `Copyright (c) 2026 jerrychan7`。

**原因**

- 没有复制参考项目的代码，许可证可以独立选择。
- MIT 简单，允许别人直接复用、修改和再分发，适合这个装在自己手机上用的项目。

**后果**

- 以后如果复制了外部 GPLv3 代码，受影响的文件要单独标注并按 GPLv3 处理，不能整包继续当作 MIT 分发。
- 新增源文件不需要逐个写版权头，仓库根目录的 LICENSE 覆盖整个项目。

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

## D-039 Firefox 从默认安装范围移除

**决策**

`desk setup` 的固定包列表不再包含 `firefox`；桌面本身仍可用，需要浏览器时由用户自行 `pkg install firefox`。已经安装的 Firefox 不卸载、不改配置。

**原因**

- 桌面启动和会话只依赖 `x11-repo`、`termux-x11-nightly`、`xfce4`、`dbus`、`termux-am`；Firefox 是应用层选择，不是桌面必需。
- 默认安装越少，首次 setup 越快，需要维护的包越少；浏览器版本交给用户和 Termux 包系统。
- M1 的浏览器验收仍用原生 Firefox，只是不再自动安装。

**后果**

- `desk setup` 不再安装 Firefox，`desk doctor` 也不再检查 Firefox；需要排查加载问题时手动运行 `firefox --version`。
- 文档里的安装范围与 M0 的描述一致（M0 本来就不安装 Firefox）。
- 已安装的设备不受影响；`desk update`、`desk uninstall --purge` 的行为不变。

## D-040 VNC 接入选用 TigerVNC，支持独立会话与镜像两种模式

**决策**

VNC 接入使用 Termux x11 仓库的 `tigervnc`（已确认 1.16.2-1 可用）。提供两种模式：

- 独立会话：`Xvnc :N`（N ≥ 1）自带 X server，运行独立的 XFCE 会话，可多开。Termux 的 `tigervnc` 包把独立服务器命名为 `Xvnc`，没有 `Xtigervnc` 这个名字。
- 镜像模式：`x0vncserver -display :0` 抓取现有 termux-x11 桌面。已确认 1.16.2-1 包内含 `x0vncserver`，因此镜像模式不需要 `x11vnc` 兜底；desk 实现见 D-044。

`desk vnc setup` 安装并校验 `tigervnc`（`Xvnc`、`vncpasswd`、`x0vncserver`）；`desk setup --vnc` 复用同一段逻辑。包内没有 `vncviewer`，连接端使用任意 VNC viewer。

默认值：`:1`、`1280x720`、24 位色、只绑 `127.0.0.1`。默认无密码（`-SecurityTypes None`，仅回环可用）；给出 `--password-file` 时改用 `VncAuth`。

**原因**

- `Xvnc` 自带 X server，独立会话的分辨率、生命周期与 termux-x11 解耦；`:N` 编号天然支持多开。
- `x0vncserver` 能在不重启桌面的情况下把现有 `:0` 投出去，适合“远程看一眼当前桌面”。
- 独立服务器的二进制名是 `Xvnc`；Termux 包不提供 `vncviewer`，连接端需要单独的应用。
- 只绑回环 + SSH 隧道，避免把未认证的 VNC 端口暴露到网络。

**后果**

- 已确认 Termux 包带 `x0vncserver`，镜像模式不需要 `x11vnc`；镜像模式的 desk 命令见 D-044。
- VNC 协议不协商帧缓冲尺寸，初始尺寸由 `-geometry` 决定；运行中改尺寸依赖 `ExtendedDesktopSize`，需要实测后才能承诺。
- password-file 是 `vncpasswd` 生成的二进制文件，VncAuth 只看前 8 个字符，不能手写。
- 镜像模式下显示尺寸跟随 `:0`（当前设备为 2248x2373），不能单独设置。

**验证（2026-11-13，设备 dev 副本）**

- `desk vnc setup` 在设备上识别 `Xvnc`、`vncpasswd`、`x0vncserver`，不安装已在的包。
- `desk vnc start` 在 `:1` 启动，`Xvnc :1 -geometry 1280x720 -depth 24 -localhost -SecurityTypes None` 的 PID 与 pgid 一致；TCP `127.0.0.1:5901` 可连接；`xwininfo -display :1 -root` 为 1280x720、IsViewable；截图确认 XFCE 桌面正常渲染。
- `desk vnc stop` 后状态文件删除，`desk vnc status` 以非零退出并报告未运行；会话日志和脚本保留在 state 目录。
- 镜像模式只用 `x0vncserver` 可用性验证过；desk 命令的实现与复测见 D-044。

## D-044 VNC 镜像 :0 用 x0vncserver，display 与 --mirror 显式选择

**决策**

- `desk vnc start --display :0 --mirror` 用 `x0vncserver -display :0` 镜像 termux-x11 桌面；只有 `:0` 允许 `--mirror`。
- `:0` 不允许新起 `Xvnc`：`desk vnc start --display :0`（不带 `--mirror`）报错并提示加 `--mirror`，或换一个 display 起独立会话。
- 其他 `:N`（N ≥ 1）仍是 `Xvnc` 独立会话，默认 `:1`；默认值不变。
- 镜像状态单独记录在 `mirror-0.json`（display、pid、port、started_at），日志 `mirror-0.log`；`desk vnc status/stop --display :0` 自动识别镜像。
- `desk vnc web start --display :0` 在镜像未运行时自动启动镜像（与 `:1` 自动起会话一致）。
- 镜像参数：`-rfbport 5900 -localhost -AlwaysShared -UseBlacklist 0 -SecurityTypes None -desktop "host :0"`；`--view-only` 时再加 `-AcceptKeyEvents 0 -AcceptPointerEvents 0 -AcceptCutText 0`，只观看不注入输入。不提供密码与 geometry（跟随 `:0`）。
- `desk vnc stop --display :0` 先停该 display 的 web 代理，再停镜像；`desk stop` 不碰镜像，也不碰 termux-x11 会话。

**原因**

- `:0` 的 X server 由 termux-x11 拥有，同一个显示号不可能再起一个 Xvnc；镜像必须挂到已有 server。
- 镜像与独立会话语义不同（同一桌面 vs 新桌面）；显式 `--mirror` 避免“选 :0 却新起 server”的歧义。曾考虑让默认 `:0` 自动镜像，最终按用户决定保留 display + `--mirror` 的显式写法。
- 镜像状态与会话状态分开，避免 `desk status` 把镜像误当成可 stop 的会话；镜像由 `desk vnc status` 展示。
- 镜像的键鼠输入会直接作用于设备桌面；`--view-only` 关闭键盘、鼠标与剪贴板注入，适合演示/旁观。`desk vnc web start --view-only` 在镜像尚未启动时按只读模式启动它。

**后果**

- 镜像分辨率跟随 `:0`（当前 2248x2373），不能单独设 geometry；浏览器侧缩放与带宽自行处理。
- 镜像独立于 desk 会话状态；`desk stop --display :0` 与 `desk stop --all` 会先停 web、再停镜像（[D-045](decisions.md)），`desk vnc stop --display :0` 仍可只收 VNC。
- `x0vncserver` 同样加 `-UseBlacklist 0`，理由同 D-043；仍只绑 `127.0.0.1`。只读模式仍允许查看和自动化截图。
- 镜像页面由 `desk vnc web start --display :0` 提供，端口仍是 6080/6081。

**验证（2026-11-14，设备 dev 副本）**

- `desk vnc start --display :0 --mirror` 输出 `Mirroring the desktop on :0.`，pid 16836、端口 5900；`desk vnc status --display :0` 退出码 0；`pgrep -af x0vncserver` 参数与设计一致。
- `desk vnc web start --display :0 --proxy rs --port 6080`：页面 200；WebSocket 6081 升级 101；经代理的 RFB 握手返回 `RFB 003.008` 与安全类型 `[1]`（None）。
- `desk vnc stop --display :0` 输出 `Stopped the web proxy for :0.` 与 `Stopped the VNC mirror on :0.`，镜像与代理退出；`:0` 会话继续运行。
- 镜像页面经 noVNC（headless Chromium）显示完整桌面（2248x2373，见 `/root/shots/novnc-of-0.png`）。


## D-041 会话以 display 编号为键，可并存

**决策**

`desk` 的会话按 display 编号存储：每个 `:N` 一份会话状态、日志、session 脚本和独立的 `XDG_RUNTIME_DIR`（`$PREFIX/tmp/termux-desktop-runtime-<N>`）。termux-x11 固定使用 `:0`；VNC 使用 `:1`、`:2`……

`desk status` 列出所有会话；`desk stop --display :N` 停止指定会话，`desk stop --all` 停止全部；不带参数时仍默认 `:0`。旧格式的 `session.json` 保持可读。

**原因**

- VNC 独立会话需要与 termux-x11 并存，单会话状态模型无法表达。
- 每个 display 独立 runtime dir，避免 XFCE/dconf/dbus 在多个会话间共享状态而互相干扰。
- display 编号是 X11 天然的唯一标识，lock/socket 路径也由它决定。

**后果**

- `Paths` 的 lock/socket/日志路径、`render_session_script`、`verify_session` 都要带 display 参数。
- 现有 `start/stop/status/logs` 的行为要迁移并补测试；旧状态文件读取逻辑要保留一段时间。
- termux-x11 目前仍只支持 `:0`；多显示号的 termux-x11 需要另行验证。

## D-043 本地回环 VNC 会话关闭 TigerVNC 黑名单

**决策**

`desk vnc start` 生成的 `Xvnc` 参数加 `-UseBlacklist 0`，不再启用 TigerVNC 的按主机黑名单。

**原因**

TigerVNC 把每次“未完成认证的连接尝试”都计入黑名单（默认阈值 5、初始超时 10 秒，重复触发会延长），同 IP 累计到阈值后，所有新连接都会被立即拒绝。我们的 VNC 会话只绑 `127.0.0.1` 且默认 `SecurityTypes None`，黑名单没有任何实际防护收益，却会被以下正常行为误伤：

- 客户端在握手完成前断开（浏览器刷新、noVNC 重试、脚本探测）。
- 同一设备上的多个客户端（浏览器、viewer、诊断脚本）共用一个 `127.0.0.1` 计数。

2026-11-13 的浏览器访问故障就是这么触发的：页面和代理都正常，但 `Xvnc` 已把 `127.0.0.1` 拉黑，经代理收到的是 `RFB 003.003\n` + `Too many security failures`，noVNC 无法完成握手，于是一直转圈。重启会话才清空黑名单。

**后果**

- 本地会话失去“暴力尝试后临时拒绝”的能力；因为只绑回环、默认无密码，这一层本来也不构成安全边界。
- 对需要密码的会话同样生效：VncAuth 的失败次数不再触发黑名单；密码强度与 `--password-file` 的保护由用户负责。
- 排查 VNC 连接问题时，先看会话日志里的 `Blacklisted` / `Too many security failures`，不要只看代理日志。

**教训（验证方法）**

不要用“只读 RFB 版本号就断开”的方式探测 VNC：每次不完整握手都会计一次失败，阈值很低时会直接把服务端弄进拒绝状态。验证要么完成完整握手（版本、安全类型、ClientInit、ServerInit）后干净断开，要么只读会话日志。

**验证**

2026-11-13 设备实测：加入 `-UseBlacklist 0` 后重启 `:1`，经 rs 代理完成完整 RFB 握手（`RFB 003.008` → `SecurityTypes None` → `SecurityResult 0` → `ServerInit`）成功；浏览器页面可正常连接。

## D-042 浏览器访问 VNC：noVNC + WebSocket 代理，默认 websockify_rs，Python 备选

**决策**

在现有 VNC 之上提供浏览器访问：noVNC 静态页面 + 一个 WebSocket→TCP 代理。noVNC pin 到 **v1.7.0**；代理支持两种实现，安装时都可以装，运行时用 `--proxy rs|py` 选择：

- `websockify_rs`（Rust，默认）：单进程、约 2 MB 内存、转发吞吐高；上游只有 v0.1.0，assets 覆盖 aarch64 / armv7 / x86_64，**缺 i686**。
- Python `websockify`（备选）：pin 0.13.0 wheel，`--web` 同时服务静态页；全架构，但空闲约 26 MB、传输时约 48–52 MB，且每个连接 fork 一个 handler（约 +17 MB/连接）。
- 缺 rs 资产或显式选择时回落到 Python；两者都不预装进 base `desk setup`，由 `desk vnc web setup`/显式命令安装。

命令与行为：

- `desk vnc web setup`：下载并校验 noVNC；代理按 `--proxy rs|py|both` 安装，默认 rs，`both` 可同时装两种，运行时再选。rs 只转发 WebSocket、不服务静态页，页面由同包安装的 `darkhttpd` 提供（`desk vnc setup` 把 `darkhttpd` 加进 VNC 依赖）；py 模式用 `websockify --web` 单进程服务页面。
- `desk vnc web start [--proxy rs|py] [--display :1] [--port 6080] [--host 127.0.0.1]`：默认 `127.0.0.1:6080`，target 按 `127.0.0.1:5900+N` 计算；会话没起时**自动 `desk vnc start`**。
- `desk vnc web stop/status`：只停/查 web 代理，**不自动停 VNC 会话**；`desk vnc stop` 则连同该 display 的 web 代理一起停（web 是 vnc 的子命令，VNC 停了页面不应留下孤儿进程）。
- `--host 0.0.0.0` 需显式指定并打印警告；本版不做 TLS/密码。
- Python 侧不额外安装解释器：`desk` 本身依赖 Python，环境里一定存在。

**原因**

- 浏览器访问的价值在于“手上只有浏览器也能看图”，noVNC 是唯一成熟的纯前端方案。
- 手机/Android 的内存压力优先于 CPU：rs 的约 2 MB 与 py 的约 50 MB 差距太大，因此默认 rs。
- 但 rs 有硬伤（见后果），所以保留 py 作为全架构/许可争议下的退路，并允许运行时切换。
- web 代理按需启动、用完 stop，避免常驻占内存；rs 无静态服务，复用 Termux 包 `darkhttpd` 比再维护一个 Python 静态服务更稳，也避免引入 issue 之外的运行时。VNC stop 一并收掉 web，避免留下占用 6080/6081 的孤儿进程。

**后果**

- `websockify_rs` 上游**没有 LICENSE 文件**（默认保留所有权利）、release 无 checksum、缺 i686。默认选它属于产品决策；我们不分发二进制，setup 时从上游下载，校验值采用 2026-11-13 记录的 TOFU sha256（见 [websockify 调研与基准](websockify.md)）。发布说明必须写明这一风险。
- Python 代理内存占用高、每连接 +17 MB，多 viewer 并发时需要提示用户。
- noVNC 的 GitHub 自动 tarball 校验值不保证永久稳定；校验失败要提示并允许显式 `--update`，不能静默换版本。
- 首次实现不覆盖：TLS、密码、镜像模式（`x0vncserver`）的 web 入口、多显示号并发。
- `desk vnc web start` 自动拉起 VNC 会话后，用户只 stop web 时 VNC 仍在跑（设计如此）；反向 `desk vnc stop` 会停掉该 display 的 web 代理，两层 stop 的差异要写清。

**验证（2026-11-13，设备 aarch64，dev 副本）**

- py/rs 的吞吐、CPU、PSS 基准与复测见 [websockify.md](websockify.md)；结论：rs 内存约 2 MB、py 约 48–52 MB，py 的 up 方向受纯 Python mask 反解限制（约 5 MB/s），down 方向 472 MB/s。
- 设备端（aarch64，dev 副本）2026-11-13 实测：`desk vnc web setup --proxy both` 安装 noVNC v1.7.0、rs 二进制（aarch64，sha256 与 TOFU 记录一致）与 Python wheel；`desk vnc web start --proxy py` 自动拉起 `:1` 会话，页面 200、`/websockify` 101；`--proxy rs` 页面 200（标题 noVNC）、WebSocket `:6081` 101，经代理发送 `RFB 003.008\n` 收到服务器版本应答；`desk vnc web stop` 后 VNC 会话仍在运行；`desk vnc web status` 在停止后退出 1。
- 2026-11-14 设备复测：rs 模式页面由 `darkhttpd/1.17` 提供（响应头 `Server: darkhttpd/1.17`），WebSocket `:6081` 返回 101；`desk vnc stop` 一并停掉 `websockify_rs`、`darkhttpd` 与会话，没有残留进程。

## D-045 `desk stop` 收敛整个 display 的 web 代理、镜像与会话

**决策**

- `desk stop --all`：先停所有 web 代理，再停所有 `:0` 镜像，最后停所有会话（`session-N.json` 列出的 display）。
- `desk stop --display :N`（以及不带参数时的 `:0`）：先停该 display 的 web 代理；`:0` 再停镜像；最后停该 display 的会话。
- `desk vnc stop` 保持“只收 VNC”：先 web，再 server/镜像，不动 termux-x11 会话。
- 菜单条目（应用集成域的配置）不随 stop 删除；容器不随 stop 停止（容器应用随会话进程组一起结束）。

**原因**

- 镜像依赖 `:0` 的 X server。会话停了镜像还挂着，只会留下死连接的 `x0vncserver` 和仍可打开的页面，状态是误导性的。
- web 代理与镜像都是 desk 起的运行态；`stop --all` 的语义是“收掉 desk 起的东西”，不应留下占用 5900/6080/6081 的孤儿进程。
- `session-N.json` 是共享内核，但 web/mirror 有独立状态，必须由 stop 显式收敛，不能只靠会话状态。
- 保留 `desk vnc stop` 作为窄命令，满足“只收 VNC、不动桌面”的场景。

**后果**

- 行为变更：0.5.x 之前 `stop --all` 不停镜像/web；从 0.6.0 起会停。发布说明必须写明。
- 某一步失败时不静默：该 display 的后续步骤仍会尝试，最终以失败项汇总报告并返回非零。
- 测试覆盖“会话不存在但有 web/mirror 状态”“只有 web/mirror 没有会话”“某一步失败仍继续其余步骤”。
- 桌面会话的 `stop --display :N` 也会停该 display 的 web 代理；`:1` 的页面不会再留在已停止的会话语义上。

## D-046 父域命令对子域做编排与聚合：只读/清理聚合，暴露显式

**决策**

- 父域（VNC）的 `setup`/`start`/`stop`/`status`/`logs` 是对子域（server、`:0` 镜像、web 代理）的便捷入口与聚合，子域命令仍然是精确原语。
- `status`、`logs` 默认聚合整个父域：`desk vnc status` 列出所有 VNC display 的会话/镜像/代理，`desk vnc logs` 默认读该 display 的会话或镜像日志，`--web` 读代理日志。
- `stop` 默认聚合（已由 D-045 落实）；`vnc stop` 停 web → server/镜像，但不动 termux-x11。
- `setup --web` 是可选便捷入口；默认 `vnc setup` 只装 server 依赖，因为安装页面与代理属于引入暴露面的前置。
- `start` 不默认启动 web 代理：`vnc web start` 会先确保 server 存在，但 `vnc start` 不反过来自动开页面；`--host 0.0.0.0` 之类扩大暴露面的参数必须显式。

**原因**

- 只读聚合没有副作用，让父域直接可用；清理聚合防止留下孤儿进程与可打开的页面；暴露类动作若默认发生，会让“起个 VNC”意外变成“开了个 HTTP 服务”。
- 默认值与文案由子域拥有，父命令只做编排和调用，避免两处 default 漂移。
- 与 §2.1 的域边界一致：跨域流程放编排层，父域命令是编排层的一部分。

**后果**

- `desk vnc status` 的默认范围从 `:1` 变为所有 VNC display；`--display` 收窄。
- `desk vnc logs` 是新的聚合入口；会话日志仍可由 `desk logs --display :N` 查看。
- `desk vnc setup --web` 等价于再跑一次 `desk vnc web setup`（默认代理 rs）。
- 新增能力测试：status 聚合、logs 聚合/`--web`、setup `--web`。

## D-047 doctor 默认聚合 VNC 检查，--vnc 只跑 VNC

**决策**

- `desk doctor` 默认包含 VNC 域的检查：`Xvnc`、`x0vncserver`、`vncpasswd`、`darkhttpd`、noVNC 页面、WebSocket 代理、镜像状态、web 状态。
- VNC 是可选后端：默认报告里缺少这些组件记 `OK`，文案是 `not installed (optional)`，不让可选依赖把健康的机器变成 warning/failure。
- `desk doctor --vnc` 与 `desk vnc doctor` 等价，只跑 VNC 检查；此时缺少 `Xvnc`/`x0vncserver`/`vncpasswd` 记 `FAILED`，缺少页面/代理或状态陈旧记 `WARNING`，退出码随 `FAILED` 非零。
- 复用同一套 `CheckResult`、摘要、颜色与退出码；`vnc doctor` 只是 VNC 域的入口，不复制渲染逻辑。

**原因**

- doctor 的职责是报告环境；默认不聚合会让“doctor 说健康、VNC 半装”成为误导。
- VNC 可选，所以聚合视图不能把它当故障；显式进入 VNC 域时，缺组件就是该域不可用，失败才符合预期，也方便脚本判断。
- 单一实现避免两套检查/退出码漂移。

**后果**

- `desk doctor` 输出增加 8 个 VNC 检查；设备上全部安装时摘要从 13 项变为 21 项。
- 新增 `desk doctor --vnc` 与 `desk vnc doctor`；README 与 tests/test_doctor.py、tests/test_cli.py 覆盖。

## D-048 desk version 默认列出 VNC 组件，--vnc 只列 VNC

**决策**

- `desk version` 的首行契约不变：第一行仍是 `desk X.Y.Z`，随后是 `source`/`installed in`（未安装时是 `not installed (running from a checkout)`）。
- 之后追加 VNC 组件段：Xvnc、x0vncserver 的版本与路径，以及 pinned noVNC、websockify_rs、websockify 的安装状态；未安装任何 VNC 组件时只打印一行 `VNC: not installed (optional).`，不刷 `not found`。
- `desk version --vnc` 只输出 VNC 组件，等同 `desk vnc version`。
- 与 doctor 的聚合原则一致：可选依赖默认不制造故障信号；显式进入 VNC 域时才是该域的回答。

**原因**

- 用户需要一条命令看到“desk + 它可选的后端”；和 `ffmpeg -version` 列编解码库同理。
- 首行保持稳定，脚本解析不被破坏；可选依赖缺失不刷屏。
- `--vnc` 作为过滤器，与 `desk doctor --vnc` 的语义一致：默认聚合，显式收窄。

**后果**

- `desk version` 输出多出空行 + VNC 段；没有 VNC 的机器多出一行 optional 提示。
- 实现上 `vnc.version_lines` 只产生内容，`desk vnc version` 与 `desk version --vnc` 共用同一实现，避免两处漂移。
- 测试覆盖：首行、VNC 段、未安装提示、`--vnc` 只输出 VNC。

## D-049 容器域增加只读聚合 `list`/`status`，删除保持显式

**决策**

- `desk container list` 只读扫描已安装的 proot-distro 容器，每个一行：名称、发行版（rootfs 的 `/etc/os-release`）、设备架构、桌面条目数、rootfs 路径。
- `desk container status NAME` 只读报告一个容器的发行版、架构、rootfs、桌面条目数，以及 `/termux`、`/home/termux`、`/sdcard` 挂载点是否就绪。
- 两个命令都不创建、不修改、不删除容器，也不调用 proot-distro 登录。
- 删除容器仍不在 desk 的命令面内：风险高，将来若做必须是显式命令并带确认，不做默认清理。

**原因**

- 容器域此前只有 `init`，用户创建容器后没有只读入口回答“有哪些容器、装的是什么、桌面条目多少”。
- 读 rootfs 的 `os-release` 比 `proot-distro login` 快且无副作用；桌面条目数就是菜单投影的来源，能解释“为什么菜单里没有这个应用”。
- 与 D-046/D-047 的聚合原则一致：只读默认聚合，破坏性动作显式。

**后果**

- `desk container list`/`status` 新增；README 同步。
- 输出不含磁盘占用（`du` 在 Termux 上慢且需要额外处理），需要时用系统工具。

## D-050 `desk app doctor` 只读检查容器应用的 Exec

**决策**

- 新增 `desk app doctor`：扫描各容器的 `.desktop` 条目，检查每个 `Exec` 的首个程序能否在容器内解析（绝对路径用 `-x`，裸名用 `command -v`），复用 `desk app refresh` 的扫描与检查代码，但不写菜单、不复制图标、不改状态。
- 默认扫描所有容器，`--container NAME` 收窄到一个容器；不存在的容器名报错。
- 有命令缺失时逐行列出 `CONTAINER/ENTRY: the command ... was not found in the container`，并以退出码 1 结束；全部能找到时退出 0。
- 检查命令本身失败（容器内 `sh` 无法启动）时不报告缺失，与 refresh 的静默策略一致。

**原因**

- 命令能否解析是“刷新时才知道”的副产物，用户不能在刷新前单独问这个问题；抽成只读命令后，菜单里应用打不开时可以先跑 doctor。
- 只读命令不碰 `~/.local/share/applications`，不会因为诊断动作改变桌面。

**后果**

- `desk app refresh` 与 `desk app doctor` 共享扫描与检查逻辑，行为要一起改。
- 进程级健康检查（启动后是否存活）仍不在范围内。

## D-051 `desk font status` 只读报告语言与字体家族

**决策**

- 新增 `desk font status`：打印 Android 语言探测结果、当前 `63-cjk-prefer.conf` 的状态（存在且由 desk setup 写入、存在但内容不是 desk 写的、不存在）、该语言下的 `sans-serif`/`serif`/`monospace` 实际命中家族。
- 文件状态用 GENERATED_MARK 判断，不修改文件；fontconfig 缺失时打印“未探测”并退出 0。
- 字体域保留 `remove` 作为清理动作，`status` 只读；没有暴露类动作（字体偏好只有 setup 写）。

**原因**

- “字体为什么长这样”此前只能靠 fc-match 或翻文件回答；本机没有 fc-match 工具，用户很难自查。
- 只读聚合与容器/应用/doctor 的模型一致（D-046/D-047/D-049/D-050）。

**后果**

- `desk font status` 新增；README 同步。
- 语言探测依赖 getprop；没有 getprop 时回退 `LC_ALL`/`LANG`，再不行显示 unknown，仍按 `zh-Hans` 解释偏好。
