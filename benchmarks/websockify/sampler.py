import os
import signal
import sys
import time

root = int(sys.argv[1]); out = sys.argv[2]
interval = float(sys.argv[3]) if len(sys.argv) > 3 else 0.05
stop = False
def h(*a):
    global stop; stop = True
signal.signal(signal.SIGTERM, h); signal.signal(signal.SIGINT, h)

def kids():
    d = {}
    for name in os.listdir('/proc'):
        if not name.isdigit(): continue
        try:
            with open('/proc/%s/stat' % name) as f: p = f.read().split()
            d.setdefault(int(p[3]), []).append(int(name))
        except (OSError, IndexError): pass
    return d

def tree():
    d = kids(); res = [root]; st = [root]
    while st:
        p = st.pop()
        for c in d.get(p, []):
            res.append(c); st.append(c)
    return res

acc = {}; iomax = {}; cmds = {}
total = 0; maxpss = maxrss = maxtree = 0
while not stop:
    pids = tree(); maxtree = max(maxtree, len(pids))
    pss = rss = 0
    for pid in pids:
        try:
            with open('/proc/%d/status' % pid) as f:
                for line in f:
                    if line.startswith('VmRSS:'): rss += int(line.split()[1])
            with open('/proc/%d/smaps_rollup' % pid) as f:
                for line in f:
                    if line.startswith('Pss:'): pss += int(line.split()[1])
            with open('/proc/%d/stat' % pid) as f: t = f.read().split()
            cur = int(t[13]) + int(t[14])
            prev = acc.get(pid)
            if prev is not None and cur >= prev: total += cur - prev
            acc[pid] = cur
            if pid not in cmds:
                try: cmds[pid] = open('/proc/%d/cmdline' % pid, 'rb').read().replace(b'\0', b' ').decode('utf-8', 'replace').strip()[:120]
                except OSError: cmds[pid] = '?'
            io = {}
            for line in open('/proc/%d/io' % pid):
                k, v = line.split(':'); io[k] = int(v)
            cur_io = iomax.get(pid, {})
            for k in ('rchar', 'wchar', 'syscr', 'syscw'):
                if io.get(k, 0) > cur_io.get(k, 0): cur_io[k] = io[k]
            iomax[pid] = cur_io
        except (OSError, IndexError, ValueError): pass
    maxpss = max(maxpss, pss); maxrss = max(maxrss, rss)
    time.sleep(interval)
sums = {k: sum(v.get(k, 0) for v in iomax.values()) for k in ('rchar','wchar','syscr','syscw')}
cm = '; '.join('%d:%s' % (p, c) for p, c in sorted(cmds.items()))
open(out, 'w').write("cpu_ticks=%d pss_kb=%d rss_kb=%d tree=%d rchar=%d wchar=%d syscr=%d syscw=%d cmds=[%s]\n" % (
    total, maxpss, maxrss, maxtree, sums['rchar'], sums['wchar'], sums['syscr'], sums['syscw'], cm))
