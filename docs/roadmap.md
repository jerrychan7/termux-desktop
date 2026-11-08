# 路线图

> 状态：方向性文档。M0 是当前目标，M1 之后是计划，不是承诺。

## 里程碑

### M0：安装固定依赖并运行 XFCE 会话

目标：在 Termux 本体上安装 M0 固定依赖，并稳定启动和停止 XFCE。

范围：

- `desk setup`：安装 `x11-repo`、`termux-x11-nightly`、`xfce4`、`dbus`、`termux-am`
- `desk doctor`
- `desk start / stop / status / logs`
- Termux:X11 + XFCE
- 进程组清理，不使用 `pkill -f` 兜底
- JSON 状态和日志

验收：

- 干净环境运行 `desk setup` 能安装固定依赖
- 缺依赖时 `desk start` 拒绝启动并提示 `desk setup`
- 桌面能显示
- `stop` 后无残留 X11/桌面进程
- 停止后能再次启动
- 失败时日志能定位到步骤
- 不需要 proot、root、GPU

### M1：Firefox

目标：在 M0 桌面上使用原生 Firefox。

范围：

- 安装并启动 Firefox
- 处理 `DISPLAY`、字体和基本桌面集成
- 不做 Chromium/Chrome

验收：

- Firefox 能正常打开网页
- 关闭 Firefox 不影响会话
- 会话停止后无残留 Firefox 进程

### M2：单容器 GUI 显示

目标：让一个 proot 容器中的 GUI 应用显示在 XFCE 上。

范围：

- `ubuntu-desktop-test` 容器
- 手写 xterm 菜单项
- `desk app launch <container>/<app>`
- `--shared-tmp`、`DISPLAY=:0`、`xhost`
- 应用进程纳入会话清理

验收：

- xterm 窗口能在 XFCE 中显示
- 能在 xterm 中操作容器内 shell
- `desk stop` 能清理容器应用
- 不使用 `--shared-home`

### M3：多容器与菜单聚合

目标：应用装在不同容器中，统一出现在桌面菜单。

范围：

- 多个 proot 容器
- 扫描容器 `.desktop`
- 生成带容器名的宿主菜单项
- `container/app-id` 标识
- 基础图标处理
- `desk app refresh`

验收：

- 两个容器各有一个 GUI 应用
- 两个应用都出现在同一个 XFCE 菜单
- 同名应用可以通过容器名区分
- 删除容器后菜单能刷新

## 后续路线

以下按优先级粗排，不绑定版本。

### 桌面与显示

- **Wayland / XWayland 支持**：当前只做 X11；Wayland 需要单独设计。
- **视觉一致性**：字体、GTK/Qt 主题、图标、光标在容器和宿主之间统一。
- **多显示器 / DPI**：外接显示器和不同缩放比例。
- **输入优化**：触屏手势、外接键盘、输入法。

### 容器与应用

- **自动应用发现**：M3 的 `.desktop` 扫描、去重、图标提取。
- **应用安装包装**：`desk app install <container> <pkg>`，仍然调用容器包管理器。
- **每容器专用桌面用户**：仅在应用拒绝 root 或需要独立用户配置时引入。
- **X11 认证收紧**：从 `xhost +` 过渡到更窄的授权方式。
- **`/tmp` 隔离**：`--shared-x11` + 每容器独立 `/tmp`，替代全量 `--shared-tmp`。
- **GPU 加速**：容器侧 Mesa/Turnip/KGSL，按容器验证。
- **音频**：PulseAudio socket 或 TCP，按需接入。
- **chroot**：不在当前路线图；proot 足够时不做。

### 工程与体验

- **TUI 安装器**：Rich + Questionary 起步，复杂后评估 Textual。
- **配置系统**：TOML/JSON，替代 Shell 配置。
- **多 profile**：触屏 XFCE、键鼠平铺 WM 等。
- **测试**：命令生成、状态机、进程清理的单元测试；真实 GUI 手工验收。
- **打包/安装**：从仓库运行到可安装的 `desk` 命令。

## 明确不做

- 整个桌面放进 proot/chroot
- 多 CPU 架构 / x86 应用 / box64/qemu
- 多 DE/WM 的完整安装支持
- Chromium / Google Chrome
- DSH 集成
- 编辑器选型（当前）
- VNC
- 主题商店 / AppStore / Wine
- 图形化安装器
- 以 proot 作为安全沙箱

## 决策门槛

新增功能前先回答：

1. 它是否破坏“一个桌面、多个应用容器”的模型？
2. 它是否需要新的安全/权限假设？
3. 它能否在 M0/M1/M2 的验收之后单独验证？
4. 它是否可以用一个手写配置先验证，再自动化？
