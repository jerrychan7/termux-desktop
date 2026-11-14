import os
import socket
import sys
import time

addr, direction, mb = sys.argv[1], sys.argv[2], float(sys.argv[3])
chunk = int(sys.argv[4]) if len(sys.argv) > 4 else 262144
total = int(mb * 1024 * 1024)
host, port = addr.rsplit(":", 1)
s = socket.create_connection((host, int(port)))
t0 = time.time(); c0 = time.process_time()
if direction == "up":
    blob = os.urandom(chunk); sent = 0
    while sent < total:
        s.sendall(blob); sent += len(blob)
    dt = time.time() - t0
    print("tcp up bytes=%d sec=%.2f MBps=%.1f cpu=%.2f" % (sent, dt, sent/1048576/dt, time.process_time()-c0))
else:
    got = 0
    while got < total:
        d = s.recv(chunk)
        if not d: break
        got += len(d)
    dt = time.time() - t0
    print("tcp down bytes=%d sec=%.2f MBps=%.1f cpu=%.2f" % (got, dt, got/1048576/dt, time.process_time()-c0))
s.close()
