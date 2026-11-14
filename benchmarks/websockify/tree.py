import os
import sys

root = int(sys.argv[1])
def childpids(p):
    out = []
    for name in os.listdir('/proc'):
        if not name.isdigit(): continue
        try:
            with open('/proc/%s/stat' % name) as f: parts = f.read().split()
            if int(parts[3]) == p: out.append(int(name))
        except OSError: pass
    return out
pids = [root] + childpids(root)
for c in list(pids):
    if c != root: pids += childpids(c)
rss = pss = ticks = 0
for pid in pids:
    try:
        with open('/proc/%d/status' % pid) as f:
            for line in f:
                if line.startswith('VmRSS:'): rss += int(line.split()[1])
        with open('/proc/%d/smaps_rollup' % pid) as f:
            for line in f:
                if line.startswith('Pss:'): pss += int(line.split()[1])
        with open('/proc/%d/stat' % pid) as f:
            t = f.read().split(); ticks += int(t[13]) + int(t[14])
    except OSError: pass
print(rss, pss, ticks, len(pids))
