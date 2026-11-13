"""基准测试目标端：发送固定字节数，退出时把总数写入 BM_DIR/target.count。"""

import os
import signal
import socket
import sys

OUT = os.environ.get("BM_DIR", ".")
port = int(sys.argv[1])
total = int(float(sys.argv[2]) * 1024 * 1024)
chunk_size = int(sys.argv[3]) if len(sys.argv) > 3 else 262144
sent = 0


def dump(*_):
    with open(os.path.join(OUT, "target.count"), "w") as f:
        f.write("%d\n" % sent)
    os._exit(0)


signal.signal(signal.SIGTERM, dump)
signal.signal(signal.SIGINT, dump)

srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", port))
srv.listen(8)
with open(os.path.join(OUT, "target.ready"), "w") as f:
    f.write("ready")

blob = os.urandom(chunk_size)
while True:
    conn, _ = srv.accept()
    try:
        while sent < total:
            conn.sendall(blob)
            sent += len(blob)
    except OSError:
        pass
    conn.close()
    with open(os.path.join(OUT, "target.count"), "w") as f:
        f.write("%d\n" % sent)
