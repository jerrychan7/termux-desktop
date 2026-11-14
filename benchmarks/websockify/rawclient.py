import base64
import os
import socket
import sys
import time

addr, mb = sys.argv[1], float(sys.argv[2])
total = int(mb * 1048576)
host, port = addr.rsplit(":", 1)
s = socket.create_connection((host, int(port)))
key = base64.b64encode(os.urandom(16)).decode()
req = ("GET / HTTP/1.1\r\nHost: %s:%s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
       "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: binary\r\n\r\n")
s.sendall((req % (host, port, key)).encode())
buf = b""
while b"\r\n\r\n" not in buf:
    d = s.recv(4096)
    if not d: raise SystemExit("no handshake")
    buf += d
head, rest = buf.split(b"\r\n\r\n", 1)
t0 = time.time(); c0 = time.process_time()
got = len(rest)
while got < total:
    d = s.recv(262144)
    if not d: break
    got += len(d)
dt = time.time() - t0
print("raw bytes=%d sec=%.2f MBps=%.1f client_cpu=%.2f" % (got, dt, got/1048576/dt, time.process_time()-c0))
