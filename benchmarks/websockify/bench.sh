#!/usr/bin/env bash
# WebSocket 代理基准：吞吐、代理进程树的 CPU/PSS、并发内存。
#
#   bench.sh ws  <py|rs> <up|down> [MB]  经代理传 WebSocket 二进制帧
#   bench.sh raw <py|rs> down [MB]       只读原始字节，避开客户端的帧解析
#   bench.sh conc <N> [MB]               N 个并发 down 客户端（固定用 py）
#   bench.sh tcp <up|down> [MB]          客户端直连目标端，测本机 TCP 上限
#
# 依赖 python3。py 代理要求 $WORK/ws 下有解包后的 websockify wheel；
# rs 代理要求 $WORK/websockify_rs 是可执行文件（按设备架构下载）。
# 默认 100 MiB、监听 127.0.0.1:16091、目标 127.0.0.1:16090，详见 README.md。
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-$HERE/work}
PYTHON=${PYTHON:-python3}
RS_BIN=${RS_BIN:-$WORK/websockify_rs}
WS_DIR=${WS_DIR:-$WORK/ws}
TARGET_PORT=${TARGET_PORT:-16090}
PROXY_PORT=${PROXY_PORT:-16091}
CHUNK=${CHUNK:-262144}
HOLD=${HOLD:-3}
SAMPLE=${SAMPLE:-0.05}
MB=${MB:-100}

TARGET=
PROXY=
SPID=

die() {
    echo "error: $*" >&2
    exit 1
}

cleanup() {
    [ -n "$SPID" ] && kill -TERM "$SPID" 2>/dev/null && wait "$SPID" 2>/dev/null
    [ -n "$TARGET" ] && kill -TERM "$TARGET" 2>/dev/null
    [ -n "$PROXY" ] && kill -TERM "$PROXY" 2>/dev/null
    wait 2>/dev/null
}
trap cleanup EXIT

start_target() { # dir
    local dir=$1
    export BM_DIR=$WORK
    rm -f "$WORK/target.ready" "$WORK/target.count"
    if [ "$dir" = up ]; then
        "$PYTHON" "$HERE/target_recv.py" "$TARGET_PORT" &
    else
        "$PYTHON" "$HERE/target_send.py" "$TARGET_PORT" "$MB" "$CHUNK" &
    fi
    TARGET=$!
    for _ in $(seq 1 100); do
        [ -f "$WORK/target.ready" ] && return 0
        sleep 0.05
    done
    die "目标端没有在 5 秒内启动"
}

start_proxy() { # mode
    local mode=$1
    case $mode in
    py)
        [ -f "$WS_DIR/websockify/__init__.py" ] ||
            die "缺少 $WS_DIR/websockify（先解包 websockify wheel）"
        PYTHONPATH=$WS_DIR "$PYTHON" -m websockify \
            "127.0.0.1:$PROXY_PORT" "127.0.0.1:$TARGET_PORT" >"$WORK/$mode.log" 2>&1 &
        ;;
    rs)
        [ -x "$RS_BIN" ] || die "缺少可执行的 $RS_BIN"
        "$RS_BIN" "127.0.0.1:$PROXY_PORT" "127.0.0.1:$TARGET_PORT" >"$WORK/$mode.log" 2>&1 &
        ;;
    *)
        die "未知代理：$mode"
        ;;
    esac
    PROXY=$!
    sleep 1.5
}

elapsed() { # ns_start ns_end -> seconds
    awk -v a="$1" -v b="$2" 'BEGIN { printf "%.2f", (b - a) / 1e9 }'
}

mode=${1:-}
case $mode in
ws)
    proxy=${2:-}
    dir=${3:-}
    [ "$dir" = up ] || [ "$dir" = down ] || die "方向必须是 up 或 down"
    mb=${4:-$MB}
    MB=$mb
    mkdir -p "$WORK"
    start_target "$dir"
    start_proxy "$proxy"
    "$PYTHON" "$HERE/tree.py" "$PROXY" >"$WORK/idle.tree"
    "$PYTHON" "$HERE/sampler.py" "$PROXY" "$WORK/sampler.out" "$SAMPLE" &
    SPID=$!
    sleep 0.3
    t0=$(date +%s%N)
    HOLD=$HOLD "$PYTHON" "$HERE/wsclient.py" "127.0.0.1:$PROXY_PORT" "$dir" "$mb" >"$WORK/client.out" 2>&1
    t1=$(date +%s%N)
    sleep 0.6
    kill -TERM "$SPID" 2>/dev/null
    wait "$SPID" 2>/dev/null
    SPID=
    "$PYTHON" "$HERE/io.py" "$PROXY" >"$WORK/io.out"
    wall=$(elapsed "$t0" "$t1")
    count=$(cat "$WORK/target.count" 2>/dev/null || echo 0)
    sample=$(cat "$WORK/sampler.out" 2>/dev/null || echo "cpu_ticks=0 pss_kb=0 rss_kb=0 tree=0")
    idle=$(cat "$WORK/idle.tree" 2>/dev/null || echo "0 0 0 0")
    read -r irss ipss _ _ <<<"$idle"
    echo "RESULT mode=$proxy dir=$dir wall=${wall}s target_bytes=$count $sample ${sample##* } idle_rss=$irss idle_pss=$ipss"
    echo "  client: $(tail -n 1 "$WORK/client.out")"
    echo "  io: $(cat "$WORK/io.out")"
    ;;
raw)
    proxy=${2:-}
    mb=${3:-$MB}
    MB=$mb
    mkdir -p "$WORK"
    start_target down
    start_proxy "$proxy"
    t0=$(date +%s%N)
    "$PYTHON" "$HERE/rawclient.py" "127.0.0.1:$PROXY_PORT" "$mb" >"$WORK/raw.out" 2>&1
    t1=$(date +%s%N)
    wall=$(elapsed "$t0" "$t1")
    count=$(cat "$WORK/target.count" 2>/dev/null || echo 0)
    echo "RAW mode=$proxy dir=down wall=${wall}s target_bytes=$count | $(tail -n 1 "$WORK/raw.out")"
    ;;
conc)
    n=${2:-2}
    mb=${3:-$MB}
    MB=$mb
    mkdir -p "$WORK"
    start_target down
    start_proxy py
    "$PYTHON" "$HERE/sampler.py" "$PROXY" "$WORK/conc.out" "$SAMPLE" &
    SPID=$!
    sleep 0.3
    pids=()
    for i in $(seq 1 "$n"); do
        HOLD=2 "$PYTHON" "$HERE/wsclient.py" "127.0.0.1:$PROXY_PORT" down "$mb" >"$WORK/c$i.out" 2>&1 &
        pids+=($!)
    done
    wait "${pids[@]}"
    sleep 0.5
    kill -TERM "$SPID" 2>/dev/null
    wait "$SPID" 2>/dev/null
    SPID=
    echo "CONC n=$n $(cat "$WORK/conc.out" 2>/dev/null)"
    for i in $(seq 1 "$n"); do
        echo "  c$i: $(tail -n 1 "$WORK/c$i.out")"
    done
    ;;
tcp)
    dir=${2:-}
    [ "$dir" = up ] || [ "$dir" = down ] || die "方向必须是 up 或 down"
    mb=${3:-$MB}
    MB=$mb
    mkdir -p "$WORK"
    start_target "$dir"
    "$PYTHON" "$HERE/tcpbench.py" "127.0.0.1:$TARGET_PORT" "$dir" "$mb" "$CHUNK"
    ;;
*)
    sed -n '2,12p' "$HERE/bench.sh" >&2
    exit 1
    ;;
esac
