# WebSocket 代理基准

测的是“浏览器 WebSocket ↔ VNC 服务器 TCP”这一跳的代理开销：吞吐、代理进程树的 CPU 和内存、并发连接的内存。**不是**真实 RFB + noVNC 的端到端测试，也没有 TLS；目标是量化代理本身，给 [D-042](../docs/decisions.md) 的选型提供数据。

## 组成

| 文件 | 作用 |
|---|---|
| `bench.sh` | 入口：`ws` / `raw` / `conc` / `tcp` 四种模式 |
| `target_recv.py`、`target_send.py` | 目标端：把收到的字节数/发完的字节数写进 `work/target.count`，用于核对流量确实到达 |
| `wsclient.py` | 最小 WebSocket 客户端：up 发带 mask 的全 0 载荷帧，down 解析帧并统计 |
| `rawclient.py` | 完成握手后只读字节流，避开客户端逐帧解析（用于测 rs 的 down 上限） |
| `tcpbench.py` | 客户端直连目标端，测本机 TCP 上限，判断代理是不是瓶颈 |
| `sampler.py` | 以固定间隔（默认 50 ms）累计进程树 CPU/PSS/RSS/IO，并记录每个子进程的 cmdline |
| `tree.py`、`io.py` | 一次性读取进程树、`/proc` IO 计数 |

## 准备

```bash
pkg install python          # 已经装过 desk 就不需要
mkdir -p benchmarks/websockify/work

# Python websockify：下载 wheel 并解包（不 pip install，不碰 numpy）
pip download --no-deps -d work websockify==0.13.0
unzip -q work/websockify-0.13.0-py3-none-any.whl -d work/ws

# websockify_rs：按设备架构下载到 work/websockify_rs 并 chmod +x
# 预期 sha256（2026-11-13 记录，见 docs/websockify.md）：
# aarch64 27c23c440a9184efbb566549c866625eab628978cc93f14111e58d6959004f08
# armv7   999aae0cd87a19b4ef16ba0a0e906ac061aa784391ae276d3631c8edaec9c905
# x86_64  65ace8a356d593ba4938ffa7899b9cb8a1ba23820d4b6b6d50434a5df5c76105
```

## 用法

```bash
cd benchmarks/websockify

./bench.sh ws py up 100      # 浏览器→目标方向，经 Python 代理
./bench.sh ws py down 100    # 目标→浏览器方向
./bench.sh ws rs up 100
./bench.sh raw rs down 100   # 只读原始字节，测 rs down 的真实上限
./bench.sh conc 2 100        # 2 个并发 down 客户端（固定用 py）
./bench.sh tcp up 100        # 客户端直连目标，测本机 TCP 上限
```

环境变量：`MB`（默认 100）、`CHUNK`（目标端分块，默认 262144）、`HOLD`（客户端传完后挂住的时间，默认 3，给采样器机会记录峰值）、`SAMPLE`（采样间隔，默认 0.05 秒）、`WORK`、`PYTHON`、`RS_BIN`、`WS_DIR`、`TARGET_PORT`、`PROXY_PORT`。

## 2026-11-13 的基线（设备 aarch64，100 MiB）

| 场景 | 实现 | 吞吐 | CPU | 峰值 PSS | 进程树 |
|---|---|---|---|---|---|
| 空闲 | Python | — | 0 | 26–27 MB RSS | 1 |
| 空闲 | Rust | — | 0 | 1.7–2.0 MB | 1 |
| up | Python | 4.8–5.1 MB/s | 20.0–20.6 s | 48.5–51.6 MB | 4 |
| up | Rust | ~365 MB/s | 0.22 s | ~2.0 MB | 1 |
| down | Python | 459–472 MB/s | 0.19–0.28 s | 48.5–51.6 MB | 4 |
| down | Rust | ~172 MB/s（raw） | 0.32 s | ~1.8 MB | 1 |
| 每多一个 viewer | Python | — | — | +17 MB | +1 进程 |

客户端直连 TCP 的上限约 1188 MB/s（up）/ 1115 MB/s（down），所以 Python 的 up 吞吐是代理侧瓶颈（纯 Python 逐字节反解 mask），不是客户端。

## 容易踩的坑

- **采样间隔**：Python 代理每个连接 fork 一个短命 handler（Python 3.14 是 forkserver）。用 `bench4.sh` 时代的 300 ms `max` 采样会漏掉 handler，导致 down 的 CPU/PSS 偏低；现在用 `sampler.py` 的 50 ms 累计，结果才可信。
- **down 的客户端开销**：`wsclient.py down` 会逐帧解析，高速时自己就是瓶颈；判断代理上限要用 `raw` 模式，并配合 `tcp` 模式对照。
- **rs 的内部分块**：上游固定 1 KiB 读块，down 方向 raw 约 172 MB/s；对 VNC 帧流够用，但会限制大流量。
- **字节数核对**：`target.count` 必须等于 `MB × 1048576`，否则结果作废（连接提前断开或客户端没发完）。
- 结果只在同机同构建下比较；换设备、换 Python 版本会变。
