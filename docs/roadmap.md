# 路线图

> 状态：方向性文档。已完成的部分只保留摘要，细节见 [决策记录](decisions.md) 和 [架构](architecture.md)。

## 已完成

- M0：`desk setup` 安装固定依赖，`desk start/stop/status/logs` 管理 XFCE 会话
- M1：原生 Firefox 在桌面上运行
- M2：`desk container init` 准备 proot 容器，`desk app launch` 让容器 GUI 显示在桌面上
- M3：`desk app refresh` 扫描容器菜单、复制图标，多个容器的应用进同一个菜单；刷新会删除不再匹配的条目与图标（菜单收敛）
- `desk pkg`：把参数原样交给容器自己的包管理器，执行后刷新菜单
- `desk install`、`desk uninstall`、`desk update`、`install.sh`；发布版本 0.0.1、0.0.2、0.3.0、0.3.1、0.3.2、0.4.0、0.5.0、0.5.1、0.6.0、0.7.0
- 0.7.1：修复配置系统与 profile 审查发现的问题（`config unset` 语义、vnc 配置回退、view_only 降级、菜单字段单行化、探针与 doctor 行为），已准备、尚未发布
- `desk upgrade`：只升级 X11 仓库的已安装包
- `desk setup` 按设备语言写入 CJK 字体偏好文件，`desk font remove` 删除
- VNC 独立会话：`desk vnc setup/start/stop/status`（`Xvnc :N`，默认 `:1`、1280x720、回环、无密码）；镜像模式 `desk vnc start --display :0 --mirror`（x0vncserver，`--view-only` 只观看）已实现；浏览器访问 `desk vnc web setup/start/stop/status`（noVNC v1.7.0 + WebSocket 代理，rs 默认、py 备选）已实现并完成设备验证（见 [D-042](decisions.md)）
- 阶段一 CJK 显示（字体、文件名、标题、剪贴板、菜单名）完成；阶段二按 D-037 本轮暂缓，见 [多语言与 CJK 显示](i18n.md)

## 后续路线

以下按优先级粗排，不绑定版本。

### 桌面与显示

- **VNC 接入**：按 [D-040](decisions.md) 使用 TigerVNC。独立模式用 `Xvnc :N` 开独立会话（可多开），`desk vnc start/stop/status` 已实现；镜像模式用 `x0vncserver` 把现有 `:0` 投出去，命令为 `desk vnc start --display :0 --mirror`（见 [D-044](decisions.md)），`desk vnc web start --display :0` 会自动启动镜像。`desk vnc setup` 与 `desk setup --vnc` 负责安装。默认 `:1`、`1280x720`、24 位、只绑 `127.0.0.1`、经 SSH 隧道访问；密码用 `--password-file`。包内没有 `vncviewer`。会话按 display 并存见 [D-041](decisions.md)。**浏览器访问**按 [D-042](decisions.md)：noVNC v1.7.0 + WebSocket 代理（默认 `websockify_rs`，Python 备选），`desk vnc web setup/start/stop/status` 已实现并完成设备端验证（页面、WS 升级与经代理的 RFB 握手）；调研与基准见 [websockify.md](websockify.md)。分辨率：`desk vnc resize WIDTHxHEIGHT` 已实现（重启会话生效）；运行中 `SetDesktopSize` 在 Xvnc 1.16.2/AArch64 上会崩溃，`:0` 镜像跟随手机、不能调整，均记入已知限制。2026-11-15 验收：`:0` 镜像 + `:1` + `:2`（带密码）三个 display 同时运行，两个 noVNC 页面并发连接（canvas 分别为 2248×2373 与 1280×720），剪贴板双向（中文）与 VncAuth 密码登录均通过；停掉 `:1` 的 web 代理不影响 `:0` 镜像。服务端内存：`Xvnc` 约 5–10 MB、`websockify_rs` 0.4–0.6 MB、`darkhttpd` <1 MB、`x0vncserver` 约 1.7 MB。仍未覆盖：TLS（经 SSH 隧道）、多 viewer 的长时间内存曲线、大尺寸画面带宽。
- **Wayland / XWayland 支持**：当前只做 X11；Wayland 需要单独设计。
- **内置 Termux:X11 构建与 Android 输入法桥**：研究由项目维护 Termux:X11 APK 的编译与打包流程，以便桥接 Android 输入法、修复输入兼容性问题并支持更多场景；在此之前使用上游 APK，输入问题记入已知限制。
- **视觉一致性**：字体、GTK/Qt 主题、图标、光标在容器和宿主之间统一。
- **多显示器 / DPI**：外接显示器和不同缩放比例。
- **输入法**：已实现基础版（[D-054](decisions.md)）：`desk setup --ime` 安装 ibus，会话按 display 启动 daemon，宿主 GTK 走 ibus、容器应用走 XIM。fcitx5 已评估并放弃（见 [fcitx5.md](fcitx5.md)、[D-055](decisions.md)）；待做：容器内更好的预编辑、浏览器端输入法。可选实验（不计入默认支持）：在容器里运行 fcitx5，经 XIM 把输入法提供给宿主应用，绕开宿主 fcitx5 的 Android 输入模式问题。2026-11-15 试验：容器内 fcitx5 可启动并注册 XIM，但宿主 GTK 与容器 xterm 都只得到原始 ASCII，XIM 未接通；暂不可用，详见 [fcitx5.md](fcitx5.md) 的「容器 fcitx5 + XIM 实验」。
- **输入优化**：触屏手势、外接键盘。

### 容器与应用

- **应用级启动包装**：已用配置 `apps` 的 `program` / `args_prefix` / `environment` 实现（[D-059](decisions.md)）；菜单项不改，`desk app launch` 与菜单启动都会套用，wrapper 脚本可把 `program` 指向容器内脚本。2026-11-16 在测试容器用 VS Code 的 `--no-sandbox` / `--user-data-dir` 验证。剩余：多入口/更复杂的包装脚本管理。
- **每容器专用桌面用户**：仅在应用拒绝 root 或需要独立用户配置时引入。
- **X11 访问限制**：当前 `termux-x11` 不校验认证，先确认它支持哪些机制，再决定是否限制谁能连 `:0`。
- **`/tmp` 隔离**：`--shared-x11` + 每容器独立 `/tmp`，替代全量 `--shared-tmp`。
- **GPU 加速**：容器侧 Mesa/Turnip/KGSL，按容器验证。
- **音频**：PulseAudio socket 或 TCP，按需接入。
- **chroot**：不在当前路线图；proot 足够时不做。

### 工程与体验

- **TUI 安装器**：Rich + Questionary 起步，复杂后评估 Textual。
- **配置系统**：YAML 已完成（默认值来自内置注释模板，用户文件整体覆盖；`version` + `apps` + `vnc`/`ime`/`fonts`）；`config init/set/unset` 先校验后原子写入、备份 `.bak`，apps 段之外的注释保留；命令行显式参数优先于配置；`config unset vnc/ime <键>` 重置为默认，`config unset fonts <家族>` 删除键回到自动探测。`desk uninstall --purge` 会一并删除配置目录与 profiles。多份配置（profile，每份一个独立 YAML 文件）已完成 v1：`DESK_PROFILE` / `--profile NAME` 选择 `profiles/NAME.yaml`，会话与菜单应用继承；尚未做：让 profile 同时切换 XFCE 的面板、主题、快捷键等设置（需要为每个 profile 准备独立的 XDG 或 HOME 目录）、profile 复制与删除、整文件注释保留式改写（当前只按段保留）。
- **两套桌面设置**：触屏用 XFCE、键鼠用平铺窗口管理器；等 profile 能切换 XFCE 配置之后再评估。
- **应用健康检查**：`desk app refresh` 检查每个 `Exec` 的首个命令能否解析；`desk app doctor --start [--timeout SECONDS]` 会按 `desk app launch` 的命令真实启动每个应用，超时后结束进程组，仍在运行或退出 0 记成功，非零退出打印 stderr 摘要（[D-059](decisions.md)）。剩余：GUI 窗口是否真的出现、长时间稳定性、默认视图是否采样启动结果。
- **截图验证**：用 X11 的截图工具抓取窗口内容，作为字体和界面的验收证据，替代只靠人眼目测。已在 termux-x11（`:0`）上验证可用：ImageMagick 的 `import -window root` 抓全屏，`xdotool search` 找到窗口 id 后 `import -window <id>` 抓单个窗口，`xwininfo` 可查看窗口树。
- **测试**：命令生成、状态机、进程清理的单元测试；真实 GUI 手工验收。

### 暂缓

- **XFCE 界面多语言**：阶段一（CJK 显示）已完成；阶段二验证后按 [D-037](decisions.md) 本轮暂缓——边际收益递减，继续投入不划算，后续得空再评估，届时可自行编译 XFCE/X11 相关组件增强体验。冻结的 shim 实验起点在 `scripts/frozen/l10n-shim/`。见 [多语言与 CJK 显示](i18n.md)。

## 明确不做

- 整个桌面放进 proot/chroot
- 多 CPU 架构 / x86 应用 / box64/qemu
- 多 DE/WM 的完整安装支持
- Chromium / Google Chrome
- DSH 集成
- 编辑器选型（当前）
- 主题商店 / AppStore / Wine
- 图形化安装器
- 以 proot 作为安全沙箱

## 决策门槛

新增功能前先回答：

1. 它是否破坏“一个桌面、多个应用容器”的模型？
2. 它是否需要新的安全/权限假设？
3. 它能否在 M0/M1/M2 的验收之后单独验证？
4. 它是否可以用一个手写配置先验证，再自动化？
