# 路线图

> 状态：方向性文档。已完成的部分只保留摘要，细节见 [决策记录](decisions.md) 和 [架构](architecture.md)。

## 已完成

- M0：`desk setup` 安装固定依赖，`desk start/stop/status/logs` 管理 XFCE 会话
- M1：原生 Firefox 在桌面上运行
- M2：`desk container init` 准备 proot 容器，`desk app launch` 让容器 GUI 显示在桌面上
- M3：`desk app refresh` 扫描容器菜单、复制图标，多个容器的应用进同一个菜单；刷新会删除不再匹配的条目与图标（菜单收敛）
- `desk pkg`：把参数原样交给容器自己的包管理器，执行后刷新菜单
- `desk install`、`desk uninstall`、`desk update`、`install.sh`；发布版本 0.0.1、0.0.2、0.3.0、0.3.1、0.3.2
- `desk upgrade`：只升级 X11 仓库的已安装包
- `desk setup` 按设备语言写入 CJK 字体偏好文件，`desk font remove` 删除
- 阶段一 CJK 显示（字体、文件名、标题、剪贴板、菜单名）完成；阶段二按 D-037 本轮暂缓，见 [多语言与 CJK 显示](i18n.md)

## 后续路线

以下按优先级粗排，不绑定版本。

### 桌面与显示

- **Wayland / XWayland 支持**：当前只做 X11；Wayland 需要单独设计。
- **视觉一致性**：字体、GTK/Qt 主题、图标、光标在容器和宿主之间统一。
- **多显示器 / DPI**：外接显示器和不同缩放比例。
- **输入优化**：触屏手势、外接键盘、输入法。

### 容器与应用

- **合并重复应用**：同一个应用装在多个容器时只留一个菜单项。
- **应用级启动包装**：在 desk 侧声明应用的额外参数、环境变量或 wrapper。容器 `.desktop` 里的 Exec 直接指向容器内路径，经 `desk app launch` 执行时不会经过包装脚本；测试容器里的 VS Code 遇到的 root 与 sandbox 限制属于这类缺口。
- **每容器专用桌面用户**：仅在应用拒绝 root 或需要独立用户配置时引入。
- **X11 访问限制**：当前 `termux-x11` 不校验认证，先确认它支持哪些机制，再决定是否限制谁能连 `:0`。
- **`/tmp` 隔离**：`--shared-x11` + 每容器独立 `/tmp`，替代全量 `--shared-tmp`。
- **GPU 加速**：容器侧 Mesa/Turnip/KGSL，按容器验证。
- **音频**：PulseAudio socket 或 TCP，按需接入。
- **chroot**：不在当前路线图；proot 足够时不做。

### 工程与体验

- **TUI 安装器**：Rich + Questionary 起步，复杂后评估 Textual。
- **配置系统**：TOML/JSON，替代 Shell 配置。
- **多 profile**：触屏 XFCE、键鼠平铺 WM 等。
- **应用健康检查**：`desk app refresh` 已检查每个 `Exec` 的首个命令能否在容器里解析（绝对路径用 `-x`、裸名用 `command -v`），找不到时按条目给出可读警告；程序能否真正启动仍待做，失败原因也应从“命令不存在”扩展到启动失败。
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
