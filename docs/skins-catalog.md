# 皮肤目录

皮肤（skin）是一套完整外观：它带齐某一外观家族的主题、图标、光标、字体与壁纸变体，并指定其中一组作为默认组合。清单只声明素材来源，资产在 apply 时从上游下载。

本文件描述模型与命令行为；实现见 `src/termux_desk/catalog.py`。

## 模型

```
skin
├─ themes    主题变体（GTK + xfwm4），至少一个
├─ icons     图标变体，至少一个
├─ cursors   光标变体，可空
├─ fonts     字体变体，可空
├─ wallpaper 壁纸，可空
├─ defaults  默认选用：theme / icons / cursors / font
├─ layout    面板与工作区布局
├─ requires  运行时依赖（如 eww、Kvantum）
└─ optional  可选组件（小部件、Qt 主题），只有 --with-optional 才装
```

组件是列表，不是单个值。清单里必须带齐该家族能拿到的全部变体，让用户在安装后自由切换；默认值只是「先显示哪一个」。

## 清单

路径：`src/termux_desk/presets/skins/<id>/skin.yaml`（内置）或 `~/.config/termux-desktop/skins/<id>/skin.yaml`（用户）。

```yaml
id: nord
name: Nord
summary: 冷色系深色外观，主题来自 Nordic 与 Qogir，图标来自 kora 与 Qogir
layout: panel-nord

defaults:
  theme: Nordic-darker
  icons: kora
  cursors: Bibata-Modern-Ice

themes:
  - name: Nordic-darker
    url: https://github.com/EliverLara/Nordic/releases/download/v2.2.0/Nordic-darker.tar.xz
    sha256: 0123…（64 位十六进制）
    pick: Nordic-darker
  - name: Qogir
    url: https://github.com/vinceliuice/Qogir-theme/archive/refs/tags/2025-09-01.tar.gz
    sha256: 4567…
    pick: Qogir-theme-2025-09-01/Qogir-Dark

icons:
  - name: kora
    url: https://github.com/bikass/kora/archive/refs/tags/v2.0.6.tar.gz
    sha256: …
    pick: kora-2.0.6/kora
  - name: Qogir
    url: …
    sha256: …
    pick: …

cursors:
  - name: Bibata-Modern-Ice
    url: …
    sha256: …
    pick: …

fonts:
  - name: MorePerfectDOSVGA
    url: …
    sha256: …
    pick: …

wallpaper:
  url: …
  sha256: …
  pick: Extras/Backgrounds/Wallpaper
  target: Bubbles.png

requires: []
optional: []
notes: ""
```

字段约束：

- `id` 必须与目录名一致；`name` 是显示名。
- 每个变体必须有 `name` 与来源：`url:` + `sha256:`（下载）或 `path:`（本地构建产物，如 `desk skin build` 的输出）。`url:` 缺 sha256 时必须显式带 `--allow-unverified` 才能下载。
- `defaults` 里的每个键必须能在对应列表里找到；`defaults.theme`、`defaults.icons` 必填（至少一个变体）。
- 变体名在同一列表内唯一，且符合 `^[A-Za-z0-9][A-Za-z0-9._+-]*$`。
- `layout` 引用 `presets/layout/<name>.yaml`；`stock` 皮肤可以只有外观，没有布局时必须能独立 apply。
- `requires` 是运行时依赖名（`eww`、`kvantum`），`optional` 是可选组件名；两者都在 `skin list` 里显示。

## 目录内容

内置皮肤（`src/termux_desk/presets/skins/`）尽量把家族里能拿到的变体写全，默认组只是其中一组：

| id | 主题 | 图标 | 光标 | 说明 |
| --- | --- | --- | --- | --- |
| `nord` | 25 | 10 | - | Nordic 的 16 个发布变体 + Qogir 的 9 个构建变体；图标含 kora 与 Qogir 9 套 |
| `materia` | 6 | 17 | 6 | Materia 的全套配色/宽度变体、Tela-circle 的 17 套、Bibata 的 6 套 |
| `win95` | 1 | 2 | 3 | Chicago95 的主题、两套图标、三套光标、DOS 字体与整目录壁纸 |
| `win10` | 1 | 1 | - | Windows-10 主题与图标（B00merang-Project），可选组件 kvantum |
| `whitesur` | 12 | 9 | - | WhiteSur 的 Dark/Light × 普通/solid × 普通/hdpi/xhdpi 主题与 9 套基础配色图标（-light/-dark 是指向同族目录的软链，不能单独安装） |

需要构建的变体（Qogir、Materia、Tela-circle、WhiteSur、WhiteSur 图标）在清单里用 `path:` 指向 `~/.local/share/termux-desktop/builds/<recipe>/<variant>`，先运行 `desk skin build presets/builds/<recipe>.yaml --yes`。

## 命令

```
desk skin list                       # 列出皮肤、变体数量、默认组体积、整套体积、依赖
desk skin show ID                    # 逐组件列出名称、来源、sha256、默认标记与差异说明
desk skin apply ID                   # 只装 defaults 那一组，写入 xfconf，再应用布局
desk skin apply ID --theme NAME      # 换主题；没装就下载，已装只改 xfconf
desk skin apply ID --icons NAME      # 换图标，同上
desk skin apply ID --cursors NAME    # 换光标，同上
desk skin apply ID --all             # 装齐该皮肤全部变体（不改变默认选择）
desk skin apply ID --with-optional   # 额外安装 requires/optional 里的组件
desk skin apply ID --dry-run         # 只校验清单与选择，不下载、不写盘
desk skin reset                      # 还原外观与布局
```

省略 ID 时使用配置里的 `skin:`。

## 惰性安装

- `apply` 默认只安装 `defaults` 选中的变体（以及壁纸、布局）。
- `--theme` / `--icons` / `--cursors` 指定另一个变体时，若该变体尚未安装就先下载，然后写入 xfconf；已安装则只改值。
- `--all` 安装全部变体但不改默认值。
- 下载缓存与校验沿用现有资产机制：HTTPS、sha256、单文件上限 64 MiB、总时长 30 分钟，失败不落盘。

体积在 `list` 里分两列显示：默认组（apply 实际下载量）与整套（--all 的总量）。用户按需选择。

## 可选组件

`requires` 是运行皮肤时缺了会少东西的外部程序（如 eww、Kvantum）。默认不安装；`--with-optional` 时 desk 先检查是否已有，缺失则调用配方安装（配方与皮肤清单分离，放在 `presets/optional/`）。

缺依赖不构成不提供皮肤的理由：`skin apply` 在缺依赖时只跳过对应组件并汇总报告，不判整次失败。

配方放在 `presets/optional/<名字>.yaml`，用户也可以在 `~/.config/termux-desktop/optional/` 放同名文件覆盖：

```yaml
name: eww
summary: 这套皮肤用到的部件守护进程
packages: [eww]          # 用 pkg install 安装的 Termux 包
commands:                # 装完包之后执行的命令，按顺序
  - mkdir -p ~/.config/eww
```

只写 `packages: []` 也能当“检查已有的外部程序”用：`install` 会先用 `command -v` 与 `dpkg-query` 判断，缺了才装。内置配方 `eww.yaml`、`kvantum.yaml` 目前只是占位（Termux 仓库里没有对应包），装不上时打印 `optional eww: could not be installed, skipped`，apply 仍算成功。

## 迁移

旧的三套皮肤 id（`chicago95`、`materia-bibata`、`nord-dark`）不再保留别名，统一为皮肤目录里的 `win95`、`materia`、`nord`。`desk skin appearance` 与 `desk skin layout` 子域保留，用于直接调试单个清单。
