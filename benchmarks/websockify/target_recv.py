"""基准测试目标端：接收字节，退出时把总数写入 BM_DIR/target.count。"""

import os
import signal
import socket
import sys

OUT = os.environ.get("BM_DIR", ".")
port = int(sys.argv[1])
total = 0


def dump(*_):
    with open(os.path.join(OUT, "target.count"), "w") as f:
        f.write("%d\n" % total)
    os._exit(0)


signal.signal(signal.SIGTERM, dump)
signal.signal(signal.SIGINT, dump)

srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", port))
srv.listen(8)
with open(os.path.join(OUT, "target.ready"), "w") as f:
    f.write("ready")

while True:
    conn, _ = srv.accept()
    while True:
        chunk = conn.recv(262144)
        if not chunk:
            break
        total += len(chunk)
    conn.close()
    with open(os.path.join(OUT, "target.count"), "w") as f:
        f.write("%d\n" % total)
