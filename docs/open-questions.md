# 未决问题

这些问题尚未形成最终决策。实现时先按文档中的默认假设推进，遇到阻塞再回到这里。

## Q-01 命令和项目命名

当前工作名是 `desk`，仓库名是 `termux-desktop`。

需要确认：

- 可执行命令叫 `desk`、`termux-desk` 还是其他
- Python 包名和仓库名是否保持一致
- 是否要避免和参考项目 `termux-desktop` 混淆

默认假设：先使用 `desk` 作为命令名，其他命名后置。

## Q-02 运行时安装布局

状态：已决，见 D-025。

采纳的布局：

```text
$PREFIX/bin/desk
$PREFIX/lib/termux-desktop/{src,templates}
$PREFIX/lib/termux-desktop/VERSION
```

未采用的候选：`~/.local/bin`（不在 Termux 默认 PATH 上）、Python `site-packages`（和 Python 版本绑定）。

`desk uninstall` 已经实现；`desk update` 和 `install.sh` 的发布流程见 D-026、D-027。

## Q-03 配置、状态和日志路径

当前实现：

```text
~/.local/state/termux-desktop/session.json
~/.local/state/termux-desktop/desk.log
~/.local/state/termux-desktop/session.log
```

- 状态是 JSON（version 2），记录进程组、boot id、会话标识和会话进程启动时间。
- `session.log` 每次 `desk start` 追加一行分隔标记，不轮转。
- `desk.log` 记录 `setup`、`start`、`stop` 的结果。
- 配置文件尚未实现；固定值写在 `src/termux_desk/config.py`。

需要确认：

- 是否需要配置文件（TOML 或 JSON），以及是否只保留固定配置
- 状态放 `~/.local/state` 还是 `$PREFIX/var/lib/termux-desktop`
- 日志是否按会话分文件或轮转
- `desk.log` 是否需要保留

## Q-04 M2 之后第一个真实 proot 应用

M2 先用 `xterm` 验证容器 GUI 能否显示。

状态：`xterm` 已在 `ubuntu-desktop-test` 中显示成功。

之后第一个真实应用未定，候选：

- 文件管理器
- LibreOffice
- GIMP
- 其他日常 GUI 工具

原则：先选依赖少、启动快的应用，避免一开始就处理 GPU、音频或复杂 DBus 依赖。

## Q-05 什么时候需要每容器专用桌面用户

M2 起使用容器 root。

需要明确何时引入专用用户：

- 应用拒绝 root
- 需要独立的用户级配置
- 需要区分包管理 root 和桌面用户
- 需要不同的 home 权限

如果引入，需要先验证：

- UID 映射
- 用户绑定目录的写权限
- X11 认证
- GPU/音频设备权限

## Q-06 自动扫描的范围

M3 计划扫描容器 `.desktop`，但范围未定。

需要确认：

- 只扫 `/usr/share/applications`，还是也扫 `~/.local/share/applications`
- 是否处理 `NoDisplay=true`
- 是否处理 `Terminal=true`
- 如何解析带字段代码的 `Exec`
- 如何处理同一个应用在多个容器中的情况
- 图标复制还是只使用图标名（已定：复制，见 D-028）

默认假设：M3 只扫系统级 `.desktop`，只支持简单 `Exec`，复杂应用用手写覆盖。

## Q-07 X11 认证如何收紧

实测结果：`termux-x11 :0` 启动时不带认证参数，容器里不设置 `XAUTHORITY`、也不运行 `xhost`，照样能连接并显示。所以 M2 不需要 `xhost +`；问题变成“要不要限制谁能连 `:0`，以及怎么限制”。

候选：

- 确认 `termux-x11` 是否提供认证参数
- 通过 `XAUTHORITY` 分发 cookie
- 限制到特定容器或用户

需要先确认 `termux-x11` 本身支持哪些机制。

## Q-08 `/tmp` 隔离方案

M2 使用 `--shared-tmp`。M3 计划改成 `--shared-x11` + 每容器独立 `/tmp`。

实测：`--shared-tmp` 展开为 `--bind=$PREFIX/tmp:/tmp`，共享整个 `$PREFIX/tmp`；`--isolated` 下不声明时，容器 `/tmp` 是容器自己的目录。另外，因为 `/termux` 挂了整棵 Termux 目录，容器仍能从 `/termux/files/usr/tmp` 访问同一份内容，所以换 `--shared-x11` 只能减少临时文件互相污染，不能减少容器能访问的文件。

需要验证：

- `termux-x11` 的 socket 是否只依赖 `.X11-unix`（`--shared-x11` 只挂这个目录）
- 独立 `/tmp` 下 `XDG_RUNTIME_DIR` 如何设置（M0 当前使用 `$PREFIX/tmp/termux-desktop-runtime`，权限 0700；M2/M3 采用独立 `/tmp` 时仍需确认如何调整）
- 是否还有应用依赖宿主 `/tmp` 的其他 socket
- 是否需要额外 bind

## Q-09 安装器 TUI 的范围

已决定先 Rich + Questionary，后评估 Textual。

未定：

- TUI 是 `desk setup` 的界面，还是独立命令
- 是否支持 `--plain` 和 `--yes`
- 是否需要安装前 Review 页面
- 是否保存进度以支持失败续跑

## Q-10 许可证

当前没有复制参考项目代码，许可证待定。

如果后续：

- 只参考思路：许可证可独立选择
- 复制 GPLv3 代码片段：需要按 GPLv3 评估
- 直接分发参考项目的修改版：需要遵守 GPLv3

在复制任何代码前先确认。

## Q-11 Termux:X11 APK 如何提示安装

`desk setup` 不安装 APK；`doctor` 检查包是否可见，并打印下载地址。

M0 的决定：

- `doctor` 只打印下载地址，不调用 `termux-open-url`。
- Android 11 之后的包可见性限制会隐藏普通构建的应用包名，因此 `pm list packages` 里看不到时只给警告，不算失败。
- `desk start` 仍然执行 `am start`；失败时提示用户自己打开 Termux:X11 App，并说明 Android 可能限制后台启动。

仍未决定：

- 是否检测 sharedUid 构建。
