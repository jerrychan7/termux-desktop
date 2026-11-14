# fcitx5 的候选窗为何不显示，以及 desk 为何不采用 fcitx5

状态：已确认（2026-11-15）。结论：fcitx5 在 Termux 的 X11 会话里能启动、能提交中文，但预编辑与候选窗从不会被绘制，无法选字；这是 fcitx5 在 Android 目标上的默认输入模式与桌面 UI 插件不兼容，desk 无法在应用层修复。因此输入法继续采用 [D-054](decisions.md) 的 ibus 方案，理由见文末。

## 现象

在 `:2` 的 VNC 会话里安装并启动 fcitx5（`fcitx5` 5.1.23-1、`fcitx5-chinese-addons` 5.1.15、`fcitx5-gtk3` 5.1.7），切换引擎到 `pinyin` 后：

- 输入 `nihao` 再按空格，GTK 输入框会提交「你好」，说明引擎与键事件链路正常；
- 但打字过程中输入框里看不到预编辑，屏幕上也没有候选窗；
- `xwininfo -root -tree` 始终只有 `Fcitx5 Input Window`（`1x1+0+0`，`Map State: IsUnMapped`）；
- 终端里走 XIM（`XMODIFIERS=@im=fcitx`）不生效，因为会话只有 `C.UTF-8`，没有 XIM 可用的 locale。

## 排查过程

1. 先排除焦点与前端问题：`fcitx5-diagnose` 显示输入上下文 `program:gtk3-demo frontend:dbus focus:1`，GTK 与 Qt 前端都正常注册；`gtk-query-immodules-3.0` 里也有 `im-fcitx5.so`。
2. 用 `fcitx5 --verbose "default=5"` 启动：按键事件确实到达 fcitx（`result:1`，被 pinyin 消费），但 `classicui` 只有启动时的一行 `Created classicui for x11 display::2`，没有任何 `Update component:` 日志。
3. 查询 fcitx5 的 DBus 控制器：

```sh
dbus-send --session --dest=org.fcitx.Fcitx5 --type=method_call --print-reply \
  /controller org.fcitx.Fcitx.Controller1.CurrentUI
```

返回空字符串。也就是说 **UserInterfaceManager 没有选中任何 UI 插件**，`update` 从未被派发到 classicui，候选窗自然不会出现。

4. 逐一排除启动参数：plain、`-u classicui`、`--disable kimpanel,virtualkeyboard` 只留 classicui、只留 virtualkeyboard，四种方式的 `CurrentUI` 都是空串；日志里也没有 `Switching UI addon to ...`。

## 根因

两个条件同时成立：

1. **fcitx5 把 Termux 判定为 Android，默认使用屏幕键盘模式。**
   `src/lib/fcitx/instance_p.h:163`：

   ```cpp
   InputMethodMode inputMethodMode_ = isAndroid()
       ? InputMethodMode::OnScreenKeyboard
       : InputMethodMode::PhysicalKeyboard;
   ```

   `isAndroid()` 在 `src/lib/fcitx-utils/misc.h:185` 是编译期 `__ANDROID__` 判断；Termux 包必然命中，所以实例模式是 `OnScreenKeyboard`。

2. **UI 插件必须与实例模式同类型才会被选中。**
   `src/lib/fcitx/userinterfacemanager.cpp` 的 `isUserInterfaceValid()` 只接受：

   - `UIType::OnScreenKeyboard` 且实例是 `OnScreenKeyboard`，或
   - `UIType::PhyscialKeyboard` 且实例是 `PhysicalKeyboard`。

   而桌面经典 UI 的 `classicui.conf` 没有 `UIType`，默认是 `PhyscialKeyboard` —— 在 `OnScreenKeyboard` 模式下永远不合法。唯一类型合法的 `virtualkeyboard` 是 DBus 虚拟键盘，需要外部宿主，`VirtualKeyboard new owner:` 为空，`available()` 不成立。

结果：`uis_` 里没有任何 UI 通过校验，`ui_` 保持 `nullptr`，`CurrentUI` 为空；`ClassicUI::update()` 永不被调用。这不是候选窗绘制失败，而是 **UI 插件根本没有激活**。

补充验证：把 `classicui.conf` 临时加上 `UIType=OnScreenKeyboard` 后，`CurrentUI` 本可变为 classicui，但 fcitx5 立刻段错误——`updateVirtualKeyboardVisibility()` 会按 `VirtualKeyboardUserInterface*` 强转该对象，classicui 并不是这个类型。该修改已还原。

## 为什么不用 fcitx5

- **候选窗不可显示就等于不可用。** 用户看不到候选、无法选字，只能接受默认候选；这不能作为产品输入法。
- **没有运行时开关。** fcitx5 的 CLI 只有 `-u/--enable/--disable`，没有输入模式覆盖；改 addon 元数据会崩溃。要修必须改 fcitx5 源码或打包（让 Termux 的桌面会话使用 `PhysicalKeyboard` 模式），desk 无法在用户态解决。
- **现有 ibus 方案已覆盖全部验收点。** [D-054](decisions.md)：宿主 GTK 走 ibus 模块、容器应用走 XIM、每个 display 一个 daemon，宿主与容器都能真实打出「你好」，并且已经产品化（`desk setup --ime`，0.6.0）。
- **同时维护两套输入法收益为负。** 多一套包、生命周期、文档和测试面，只为一个当前不可用的备选。
- **可逆。** 如果 fcitx5 上游将来提供输入模式覆盖，或 Termux 打包修正了 Android 判定，可以重新评估；那时再考虑用它替换 ibus。

## 影响

- desk 不安装、不检测、不配置 fcitx5；用户自行安装 fcitx5 不会破坏 desk 会话，但其候选窗在这个环境里仍然不会出现。
- 输入法文档与路线图以 ibus 为准；fcitx5 作为已评估备选记录在本文件。

## 复现要点

```sh
pkg install fcitx5 fcitx5-chinese-addons fcitx5-gtk3
desk vnc start --display :2
# 在 :2 的 D-Bus 里启动
env DISPLAY=:2 DBUS_SESSION_BUS_ADDRESS=... fcitx5 -r
fcitx5-remote -s pinyin
# 在 GTK 输入框里输入 nihao + 空格：能提交「你好」，但没有候选窗
dbus-send --session --dest=org.fcitx.Fcitx5 --type=method_call --print-reply \
  /controller org.fcitx.Fcitx.Controller1.CurrentUI   # 空串
```

## 容器 fcitx5 + XIM 实验（2026-11-15）

背景：宿主 bionic 的 fcitx5 因 Android 输入模式判定无法显示候选窗（见上）。可选假设是把 fcitx5 装进 glibc 容器，用 XIM 给宿主 X11 客户端提供输入法。

环境：

- desk 0.6.0，`:0` 会话。
- `ubuntu-desktop-test`（Ubuntu 26.04.1，glibc）：fcitx5 5.1.19、fcitx5-chinese-addons、fcitx5-frontend-gtk3。
- 启动：`proot-distro login ubuntu-desktop-test --shared-tmp -- dbus-run-session -- env DISPLAY=:0 XMODIFIERS=@im=fcitx LANG=C.UTF-8 LC_ALL=C.UTF-8 fcitx5`。
- `xprop -root XIM_SERVERS` 为 `@server=fcitx`；`fcitx5-diagnose` 显示服务器名匹配；日志有 classicui 创建、pinyin/cloudpinyin 加载。

结果：

1. 宿主 GTK（gtk3-demo，`GTK_IM_MODULE=xim XMODIFIERS=@im=fcitx`）输入 `nihao`：得到原始 ASCII，无候选窗；`im-xim.so` 已加载；容器 fcitx 日志没有 XIM 客户端连接记录。
2. 容器 xterm（同一 `:0`，`XMODIFIERS=@im=fcitx`）输入 `nihao`：同样只得到原始 ASCII。
3. 首次启动时 XIM 被一个残留的宿主 fcitx5（bionic，宿主 D-Bus，`DISPLAY=:0.0`）占用，容器实例报 `Failed to open xim, retrying`；杀掉后容器实例成功注册 XIM，但客户端仍然连不上。
4. 容器 GTK 前端测试无效：容器没有安装 gtk3-demo，实际执行的是宿主 bionic 的 `/data/data/com.termux/files/usr/bin/gtk3-demo`；进程 maps 里是宿主 `im-fcitx5.so`。因此“容器 GTK 应用能否使用容器 fcitx5”没有得到验证。

分析：

- 宿主 ibus 的 XIM 在同样 `C.UTF-8` 下可用（见 D-054），说明客户端侧 XIM 路径可行；失败点在 fcitx5 的 XIM 服务器与客户端组合上，未进一步定位（可能是 locale/编码协商，或 Termux X server 的 XIM 实现差异）。
- 要得到可靠结论，下一步需要在容器里安装 `gtk-3-examples`，用容器自己的 GTK 应用 + 容器 fcitx5 的 GTK 前端测试；或者抓 XIM 协议包定位。

结论：容器 fcitx5 + XIM 当前不可用，不进入产品化；保留为可选实验，结果记入路线图。宿主 ibus 仍是默认方案，不受影响。

复现命令：

```sh
desk start
proot-distro login ubuntu-desktop-test --shared-tmp -- \
  apt-get install -y fcitx5 fcitx5-chinese-addons fcitx5-frontend-gtk3
proot-distro login ubuntu-desktop-test --shared-tmp -- dbus-run-session -- \
  env DISPLAY=:0 XMODIFIERS=@im=fcitx LANG=C.UTF-8 LC_ALL=C.UTF-8 fcitx5
# 焦点放到宿主 gtk3-demo（GTK_IM_MODULE=xim）或容器 xterm：输入 nihao，仍是原始 ASCII
```
