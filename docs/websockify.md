# 浏览器访问 VNC：websockify 代理调研与基准

浏览器访问 VNC 需要两样东西：noVNC（浏览器里的 VNC 客户端，静态 HTML/JS）和一个把浏览器 WebSocket 转成 TCP 的代理。VNC 输出是 RFB 二进制流，普通静态服务器转发不了，所以代理是必需的。

这份文档记录 2026-11-13 的调研与基准，用于 [D-042](decisions.md) 的决策依据。结论先行：**`desk vnc web` 默认用 `websockify_rs`，备用 Python `websockify`；安装时两者都支持，运行时用 `--proxy` 选择**。

**实现状态**：`desk vnc web` 已按本方案实现（`src/termux_desk/webproxy.py`）。2026-11-13 在设备（aarch64）验证：py 与 rs 的页面加载、WebSocket 升级，以及经代理的 RFB 版本握手均通过。
rs 只做 WebSocket→TCP 转发、不服务静态页：rs 模式的页面由 `darkhttpd` 提供（`desk vnc setup` 安装），py 模式由 `websockify --web` 单进程服务；2026-11-14 复测确认 `darkhttpd/1.17` 正常提供 noVNC 页面。

## 候选对比

| 候选 | 版本 | 许可 | Termux 架构 | 安装 | 静态页 + 转发同一进程 | 校验和 | 维护 |
|---|---|---|---|---|---|---|---|
| Python `websockify`（novnc） | 0.13.0（PyPI 2025-05-26） | LGPL-3.0 | 全（`py3-none-any`） | wheel 解包 / `pip --no-deps` | 是（`--web DIR`） | PyPI sha256 | 活跃 |
| `websockify-js` | npm 0.7.1（2015）、仓库 2023 | LGPL-3.0 | 全，但需要 Node | `npm install` | 是 | 仅 npm shasum | 停更 3 年+ |
| `websockify_rs`（bajrangCoder） | v0.1.0（2024-09-16） | **无 LICENSE 文件** | aarch64 / armv7 / x86_64，**缺 i686** | `install.sh` 或直接下二进制 | 否（不服务静态页） | 无 checksum | 2024-10 后停更 |
| `websockify-other/c` | 无 release | LGPL-3.0 | 源码编译 | 需要工具链 | 部分 | 无 | 2026-08 有提交 |
| `go-websockify` | v1.2.0（2020-12） | MIT | amd64 / arm64 | 预编译 | 否 | 无 | 停更 |
| nginx / caddy | Termux 包 | BSD / Apache-2.0 | 全 | `pkg install` | 否（只能 WS→WS，不能 WS→TCP） | 包管理 | 活跃 |

官方 Feature Matrix 明确说 Python 实现是“supported implementation”，其余是实验/参考实现；`websockify-other/c` 既不支持静态页也不支持二进制流，`websockify-js` 缺少多目标/守护化。nginx/caddy 只能做前端 TLS/认证，不能替代代理。

## 版本与校验值

- noVNC **v1.7.0**：`https://github.com/novnc/noVNC/archive/refs/tags/v1.7.0.tar.gz`，726,728 B，sha256 `b1003a11b6e6e8d8f7f5e5586daae7f8ca651d8aee0aa155ff9ac841c48f52c6`（GitHub 自动 tarball 的哈希不保证永久稳定，校验失败要给出提示并允许 `--update`）。
- Python websockify **0.13.0**：PyPI wheel `websockify-0.13.0-py3-none-any.whl`，41,537 B，sha256 `ac497a8dafc7f51d28ca989f01cdf5f8add663a497d425a56b159dcb8d6a314c`。PyPI 没有 0.13.0 的 provenance，pin sha256 是必要的。
- websockify_rs **v0.1.0**：上游 release 不带 checksum（`digest=null`），以下是 2026-11-13 首次下载后记录的 TOFU 哈希，仅用于发现文件变化，不代表上游签名：
  - `websockify_rs-aarch64` 1,438,856 B，sha256 `27c23c440a9184efbb566549c866625eab628978cc93f14111e58d6959004f08`
  - `websockify_rs-armv7` sha256 `999aae0cd87a19b4ef16ba0a0e906ac061aa784391ae276d3631c8edaec9c905`
  - `websockify_rs-x86_64` sha256 `65ace8a356d593ba4938ffa7899b9cb8a1ba23820d4b6b6d50434a5df5c76105`

**许可风险**：`websockify_rs` 仓库没有 LICENSE 文件，按默认即“保留所有权利”。它作为默认代理属于产品决策（[D-042](decisions.md)），文档与发布说明需要明确这一点；我们不分发它的二进制，setup 时从上游下载。

## 基准（2026-11-13，设备 aarch64）

方法：目标端用本机 TCP 收/发，代理监听 `127.0.0.1:16091`，每次 100 MiB；up（浏览器→目标，带 mask）与 down（目标→浏览器，不带 mask）分开测；内存取进程树 PSS，CPU 取进程树 utime+stime（`CLK_TCK=100`）；每组 3 次取中位数；目标端字节计数确认满 100 MiB。不是真实 RFB + noVNC JS，也没有 TLS。

| 场景 | 实现 | 中位吞吐 | CPU（100 MiB） | 峰值 PSS | 进程树 |
|---|---|---|---|---|---|
| 空闲 | Python | — | 0 | 26 MB RSS | 1 |
| 空闲 | Rust | — | 0 | 1.7 MB PSS | 1 |
| up | Python | 5.1 MB/s | 20.0 s | 48.5–49.1 MB | 4 |
| up | Rust | ~365 MB/s | 0.22 s | 2.0 MB | 1 |
| down | Python | 471.7 MB/s | 0.19–0.28 s | 48.5–51.6 MB | 4 |
| down | Rust | ~172 MB/s（raw 读取） | 0.32 s | 1.8 MB | 1 |

客户端直连 TCP 的上限约 1188 MB/s（up）/1115 MB/s（down），确认 Python 的 up 吞吐是代理侧瓶颈，不是测试客户端。

要点：

- **内存差距是决定性的**：Python 版空闲约 26 MB，传输时进程树 PSS 约 48–52 MB；websockify 每个连接 fork 一个 handler（`websockifyserver.py:830`，Python 3.14 用 forkserver），多一个 viewer 约 +17 MB。Rust 版约 2 MB、单进程。
- **CPU/吞吐是方向性的**：Python 在 up（带 mask 的浏览器输入）要逐字节反解，单核打满，只有 5 MB/s；down（VNC 画面帧的主方向，不带 mask）很快（472 MB/s）。Rust 在两个方向都更快，但 down 方向受它内部 1 KiB 分块影响，raw 读取约 172 MB/s，对 VNC 仍够用。
- VNC 的 up 流量只有键盘/鼠标和小段粘贴，所以 Python 的 up 短板不直接伤帧率；真正的风险是常驻内存。`desk vnc web` 因此按需启停，文档里写明内存预期。
- 首轮报告里“Python down CPU 0.07 s / PSS 35 MB”和“Rust down 17.4 MB/s”已作废：前者是 300 ms 采样漏掉短命 handler 子进程，后者测到的是测试客户端解析瓶颈。修正值见上表。

复现脚本已整理进仓库：[benchmarks/websockify](../benchmarks/websockify/README.md)（`bench.sh`、目标端、帧客户端、采样器与说明）。设备上 `bm/` 里的原始草稿可以删除。
