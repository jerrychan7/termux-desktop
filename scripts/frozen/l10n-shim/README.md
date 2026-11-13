# 冻结：gettext preload shim

**状态：冻结（FROZEN，2026-11-13）**。不接入 `desk`、`install.sh`、菜单或任何安装流程；保留为后续重新做界面本地化时的起点。

背景与结论见 [docs/i18n.md](../../../docs/i18n.md) 和 [D-037](../../../docs/decisions.md)。一句话：Termux 的 NDK 把 gettext 家族编译成返回 msgid 的内联函数，`preload` 只能翻译走了 `g_dgettext`/`g_dpgettext2` 符号的字符串，菜单栏等内联 `gettext()` 字符串 hook 不到；本轮因边际收益递减暂停。

## 文件

- `desk-l10n-shim.c`：interpose `g_dgettext`、`g_dcgettext`、`g_dpgettext2`，直接从 `.mo` 查表。简单实现：无复数、无编码转换，context 用 `context\x04msgid` 精确匹配。

## 构建

在设备上（宿主 Termux）：

```sh
clang -shared -fPIC -O2 -Wall -o desk-l10n.so desk-l10n-shim.c
```

## 环境变量

- `DESK_L10N_LANG`：语言目录名，默认 `zh_CN`。
- `DESK_L10N_DIR`：`.mo` 根目录，默认 `$PREFIX/share/locale`。
- `DESK_L10N_LOG`：设置后把每次查询打到 stderr，用于确认哪些字符串走了符号。

## 用法（当时实验）

```sh
# 取翻译数据（在 alpine2 容器里）
apk fetch thunar-lang
# 解包后把 .mo 放到宿主
cp usr/share/locale/zh_CN/LC_MESSAGES/thunar.mo "$PREFIX/share/locale/zh_CN/LC_MESSAGES/"
# 带 shim 启动 Thunar daemon（注意 daemon 自己读环境变量）
pkill -x thunar
LD_PRELOAD="$PWD/desk-l10n.so" DESK_L10N_LANG=zh_CN DESK_L10N_LOG=1 \
  thunar --daemon
```

预期：侧栏 `Places/Devices/File System`、tooltip `View Switcher`、状态栏变中文；菜单栏与右键菜单仍英文（内联 `gettext()`，无法拦截）。

## 重新投入时的注意

- shim 只能覆盖走动态符号的字符串；要覆盖菜单需要硬编码覆盖表（绑版本）或自行编译 XFCE/X11（真实 libintl）。
- 版本匹配：`.mo` 是运行时按 msgid 查表，patch 差异通常只让个别字符串回退英文。
- 不要把 `.mo` 二进制或构建产物提交进仓库。
