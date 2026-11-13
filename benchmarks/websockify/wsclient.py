import socket, sys, os, time, struct, base64
addr, direction, mb = sys.argv[1], sys.argv[2], float(sys.argv[3])
total = int(mb * 1024 * 1024)
recv_sz = int(os.environ.get("RECV", "262144"))
hold = float(os.environ.get("HOLD", "0") or 0)
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
if b" 101 " not in head.split(b"\r\n")[0]:
    raise SystemExit("bad handshake")
t0 = time.time(); c0 = time.process_time()
if direction == "up":
    fs = int(os.environ.get("FRAMESZ", "65536"))
    payload = os.urandom(fs)
    frame = b"\x82" + b"\xff" + struct.pack("!Q", len(payload)) + os.urandom(4) + bytes(len(payload))
    sent = 0; frames = 0
    while sent < total:
        s.sendall(frame); sent += len(payload); frames += 1
    s.sendall(b"\x88\x80" + os.urandom(4))
    dt = time.time() - t0; cpu = time.process_time() - c0
    print("dir=up bytes=%d frames=%d sec=%.2f MBps=%.1f client_cpu=%.2f" % (sent, frames, dt, sent/1048576/dt, cpu))
else:
    got = 0; data = rest; frames = 0; fsum = 0; fmax = 0; fmin = 1 << 62
    while got < total:
        while len(data) < 2:
            d = s.recv(recv_sz)
            if not d: raise SystemExit("closed early got=%d" % got)
            data += d
        b1, b2 = data[0], data[1]
        ln = b2 & 0x7f; off = 2
        if ln == 126:
            while len(data) < 4: data += s.recv(4096)
            ln = struct.unpack("!H", data[2:4])[0]; off = 4
        elif ln == 127:
            while len(data) < 10: data += s.recv(4096)
            ln = struct.unpack("!Q", data[2:10])[0]; off = 10
        while len(data) < off + ln:
            d = s.recv(recv_sz)
            if not d: raise SystemExit("closed early got=%d" % got)
            data += d
        op = b1 & 0x0f
        if op == 2:
            got += ln; frames += 1; fsum += ln
            if ln > fmax: fmax = ln
            if ln < fmin: fmin = ln
        data = data[off + ln:]
    dt = time.time() - t0; cpu = time.process_time() - c0
    print("dir=down bytes=%d frames=%d avg_frame=%d min=%d max=%d sec=%.2f MBps=%.1f client_cpu=%.2f" %
          (got, frames, fsum // max(frames,1), fmin if frames else 0, fmax, dt, got/1048576/dt, cpu))
if hold: time.sleep(hold)
s.close()
