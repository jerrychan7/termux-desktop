# termux-desktop

Termux 原生桌面环境，面向 Android/Termux 的通用 X11 桌面与 proot 应用集成方案。

> **状态：设计阶段。**
> 当前仓库只有设计文档，没有可执行代码，也没有提交。
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
4. **Python 负责决策，Bash 负责 exec**：主逻辑用 Python；Bash 只作为生成的薄会话脚本。
5. **不做完整发行版**：不追求多 DE、主题商店、AppStore、复杂的安装器分支。
6. **不共享 home，容器目录挂载由项目统一安排**：每个容器使用自己的 home；容器里哪些目录挂载到哪里、路径如何约定，由项目统一安排；用户可以在这些约定之上绑定自定义目录。
7. **不把 proot 当安全沙箱**：proot 只隔离应用和发行版环境，不是安全边界。

## 分阶段计划

| 阶段 | 内容 | 验收 |
|---|---|---|
| **M0** | `desk doctor` + `start/stop/status/logs`，只启动 XFCE | 桌面能显示、能干净退出、能再次启动 |
| **M1** | 加入原生 Firefox | 浏览器能正常访问网页 |
| **M2** | 一个 proot 容器 + 手写 `xterm` 菜单项 | 容器 GUI 能显示在 XFCE 上 |
| **M3** | 多容器 + 自动扫描 `.desktop` | 两个容器的应用都出现在同一个桌面菜单 |

当前只规划到 **M0**；M1 之后的阶段是方向，不是本阶段的实现承诺。

## M0 范围

命令（工作名，最终命名待定）：

```bash
desk doctor
desk start
desk status
desk stop
desk logs
```

只支持一个固定配置：

- Termux 本体
- Termux:X11，`DISPLAY=:0`
- XFCE
- APT 环境
- 不启动 proot，不启动 Firefox，不做音频/GPU/VNC

关键约束：

- 使用进程组管理会话，不依赖 `pkill -f` 作为主要清理手段
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
