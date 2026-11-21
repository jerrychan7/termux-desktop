# 皮肤目录

皮肤（skin）是一套完整外观：它带齐某一外观家族的主题、图标、光标、字体与壁纸变体，并指定其中一组作为默认组合。清单里的资产有两种来源：`url:` 变体在 apply 时从上游下载（内置皮肤都带 sha256），`path:` 变体是本地文件，通常由 `desk skin build` 在容器里构建出来；内置皮肤的这些文件不带 sha256，apply 直接读磁盘，缺了报错并提示先构建。

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
├─ layout    面板与工作区布局，可省略
├─ packages  Termux 包名列表，apply 前核对，缺了失败
├─ requires  皮肤用到的外部组件（如 eww、Kvantum）
└─ optional  额外组件（小部件、Qt 主题），与 requires 一起只在 --with-optional 时装
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

packages: []
requires: []
optional: []
notes: ""
```

字段约束：

- `id` 必须与目录名一致；`name` 是显示名。
- 每个变体必须有 `name` 与来源：`url:` + `sha256:`（下载）或 `path:`（本地文件，通常是 `desk skin build` 的产物）。`url:` 缺 sha256 时必须显式带 `--allow-unverified` 才能下载；`path:` 变体可以不带 sha256（内置皮肤的构建变体都不带），带了就校验，文件不存在时 apply 直接报错，内置皮肤的这类路径还会提示先跑 `desk skin build`。
- `defaults` 里的每个键必须能在对应列表里找到；`defaults.theme`、`defaults.icons` 必填（至少一个变体）。
- 变体名在同一列表内唯一，且符合 `^[A-Za-z0-9][A-Za-z0-9._+-]*$`。
- `layout` 引用 `src/termux_desk/presets/layout/<name>.yaml`，也可以省略 `layout:`，这时 apply 只做外观。`stock` 是外观预设（`src/termux_desk/presets/appearance/stock.yaml`），不是皮肤。
- `requires` 是皮肤用到的外部组件名（`eww`、`kvantum`），`optional` 是额外组件名；`skin list` 的 REQUIRES 列只显示 `requires`，`skin show` 才同时列出 `optional`。
- `packages` 是 Termux 包名列表，`skin apply` 在写入外观前用 `dpkg-query` 逐个核对，缺包直接失败并打印 `pkg install -y …`；`skin check` 只校验清单与选择，不检查包。

## 目录内容

内置皮肤（`src/termux_desk/presets/skins/`）尽量把家族里能拿到的变体写全，默认组只是其中一组：

| id | 主题 | 图标 | 光标 | 说明 |
| --- | --- | --- | --- | --- |
| `nord` | 25 | 10 | 2 | Nordic 的 16 个发布变体 + Qogir 的 9 个构建变体；图标为 kora 与 Qogir 的 9 个构建变体（Qogir 的配色变体是指向基础主题的相对软链薄主题，本地 `path:` 资产允许这类共享链接，见下）；光标为 Qogir 的深色、浅色两套，取自 `qogir-icons` 的构建产物 |
| `materia` | 9 | 19 | 6 | Materia 的全套配色/宽度变体、Tokyonight-Dark-Moon（`src/termux_desk/presets/builds/tokyonight.yaml`，含 hdpi/xhdpi）、Tela-circle 的 19 套、Bibata 的 6 套 |
| `win95` | 1 | 2 | 6 | Chicago95 的主题、两套图标、六套光标、DOS 字体与整目录壁纸 |
| `win10` | 1 | 1 | 1 | Windows-10 主题与 Windows-10-Icons 图标（B00merang-Project，图标仓库里不含光标）、Win10OS_Cursors 光标各一套；可选组件 kvantum |
| `whitesur` | 30 | 12 | 4 | WhiteSur 的 12 个构建变体（深/浅 × 普通/solid × 普通/hdpi/xhdpi）与 Lavanda 的 18 个构建变体（标准/紧凑 × 深/浅/默认 × 普通/hdpi/xhdpi）；图标为 WhiteSur 的 9 套基础配色与 Colloid-Purple 的 -Light/-Dark/无后缀三变体（-Dark 与无后缀变体在上游是指向 -Light 的软链，构建时换成真实目录；WhiteSur 的 -light/-dark 仍是指向同族目录的软链，不单独安装）；光标为 Fluent、Fluent-dark 与 volantes 的深/浅两套；字体为 Mononoki Nerd Font 的三个字族 |
| `otis` | 1 | 2 | 1 | Otis 的 standard-buttons 分支主题、Deepin 2022 的两套图标（Dark 变体由 `src/termux_desk/presets/builds/deepin-icons.yaml` 生成）、Layan 白色光标；可选组件 eww 与 kvantum |

`path:` 变体由 `desk skin build` 在 `ubuntu` 容器里构建，产物收进 `~/.local/share/termux-desktop/builds/<配方>/<产物>/`。配方在 `src/termux_desk/presets/builds/`，输出目录名取自配方里的 `build:` 字段（这 11 个配方与文件名相同）。命令要在仓库根目录执行，否则找不到配方文件：

| 配方（相对仓库根目录） | 产物目录 | 影响的皮肤与变体 |
| --- | --- | --- |
| `src/termux_desk/presets/builds/qogir-theme.yaml` | `~/.local/share/termux-desktop/builds/qogir-theme/` | `nord` 的 9 个 Qogir 主题（Qogir、Qogir-Dark、Qogir-Light 三套配色，各有普通、`hdpi`、`xhdpi`） |
| `src/termux_desk/presets/builds/qogir-icons.yaml` | `~/.local/share/termux-desktop/builds/qogir-icons/` | `nord` 的 9 个 Qogir 图标；`nord` 的 Qogir-dark、Qogir-light 光标也从这个目录取 |
| `src/termux_desk/presets/builds/materia.yaml` | `~/.local/share/termux-desktop/builds/materia/` | `materia` 的 6 个 Materia 主题 |
| `src/termux_desk/presets/builds/tokyonight.yaml` | `~/.local/share/termux-desktop/builds/tokyonight/` | `materia` 的 3 个 Tokyonight 主题（含 `hdpi`/`xhdpi`） |
| `src/termux_desk/presets/builds/tela.yaml` | `~/.local/share/termux-desktop/builds/tela/` | `materia` 的 19 个 Tela-circle 图标（`-dark`/`-light` 指向同族目录的软链在构建末尾换成真实内容） |
| `src/termux_desk/presets/builds/whitesur-theme.yaml` | `~/.local/share/termux-desktop/builds/whitesur-theme/` | `whitesur` 的 12 个 WhiteSur 主题 |
| `src/termux_desk/presets/builds/lavanda-theme.yaml` | `~/.local/share/termux-desktop/builds/lavanda-theme/` | `whitesur` 的 18 个 Lavanda 主题 |
| `src/termux_desk/presets/builds/whitesur-icons.yaml` | `~/.local/share/termux-desktop/builds/whitesur-icons/` | `whitesur` 的 9 个 WhiteSur 图标 |
| `src/termux_desk/presets/builds/colloid-icons.yaml` | `~/.local/share/termux-desktop/builds/colloid-icons/` | `whitesur` 的 3 个 Colloid-Purple 图标 |
| `src/termux_desk/presets/builds/volantes-cursors.yaml` | `~/.local/share/termux-desktop/builds/volantes-cursors/` | `whitesur` 的 volantes-cursors、volantes-light-cursors 两套光标 |
| `src/termux_desk/presets/builds/deepin-icons.yaml` | `~/.local/share/termux-desktop/builds/deepin-icons/` | `otis` 的 Deepin2022-Dark 图标（上游只有 Deepin2022 一套，配方复制一份并改写主题名） |

命令形如 `desk skin build src/termux_desk/presets/builds/qogir-theme.yaml --yes`，`--yes` 不能省。每次构建会替换 `builds/<配方>/<产物>/` 下已有的同名目录。

## 命令

```
desk skin list                       # 列出皮肤、变体数量、默认组下载体积、整套下载体积、依赖
desk skin show ID                    # 逐组件列出名称、来源、sha256、默认标记与差异说明
desk skin apply ID                   # 只装 defaults 那一组，写入 xfconf，再应用布局
desk skin apply ID --theme NAME      # 换主题；没装就安装（下载或读构建产物），已装只改 xfconf
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
- `--theme` / `--icons` / `--cursors` 指定另一个变体时：`url:` 变体没装就先下载；`path:` 变体必须已经由 `desk skin build` 生成，文件缺失就报错停下。装好后写入 xfconf，已安装则只改值。
- `--all` 安装全部变体但不改默认值。
- 下载缓存与校验沿用现有资产机制（只对 `url:` 变体生效）：HTTPS、sha256、单文件上限 64 MiB、总时长 30 分钟，失败不落盘。

体积在 `list` 里分两列显示：DEFAULT 是默认组要下载的体积，ALL 是 `--all` 要下载的体积。两列都只累加 `url:` 变体的 `size:`，`path:` 变体是本地构建产物，不计入；一组里没有可统计的 `url:` 资产时显示 `local`。

## 共享链接

本地 `path:` 资产（`desk skin build` 的产物）允许软链指向同一安装根下的兄弟主题：上游把 `Qogir-Dark/scalable` 之类直接链到基础主题 `Qogir`，这样的薄主题不能独立成包，但链接本身不越界。安装时每个被接受的链接都会打印一行：

```
allowed shared link scalable -> ~/.local/share/icons/Qogir/scalable
```

允许的目标只有 `~/.local/share/icons`、`~/.local/share/themes`、`~/.local/share/termux-desktop/builds` 三处；指向 `~/.ssh`、`/system` 之类的链接仍然报错。`url:` 归档保持原样：任何解析后落在主题目录之外的链接都会被拒绝。

Android 文件系统大小写不敏感，图标主题与光标主题可能落到同一个目录（`Qogir-Dark` 与光标名 `Qogir-dark`）。安装器遇到这种目录时不再跳过，而是把 `cursors/` 填进已存在的图标目录，两边共存。

## 可选组件

`requires` 是皮肤用到的外部组件（如 eww、Kvantum），`optional` 是额外组件（小部件、Qt 主题）。两者默认都不安装；加 `--with-optional` 时 desk 按 `src/termux_desk/presets/optional/<名字>.yaml` 检查，已有就跳过，缺失就调用配方安装。

`packages` 缺失会让 `skin apply` 直接失败；`requires` 与 `optional` 缺失只跳过对应组件并汇总报告，不判整次失败。

配方放在 `src/termux_desk/presets/optional/<名字>.yaml`，用户也可以在 `~/.config/termux-desktop/optional/` 放同名文件覆盖：

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
