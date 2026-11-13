import os, sys
root = int(sys.argv[1])
def kids():
    d = {}
    for name in os.listdir('/proc'):
        if not name.isdigit(): continue
        try:
            with open('/proc/%s/stat' % name) as f: p = f.read().split()
            d.setdefault(int(p[3]), []).append(int(name))
        except (OSError, IndexError): pass
    return d
d = kids(); pids = [root]; st = [root]
while st:
    p = st.pop()
    for c in d.get(p, []):
        pids.append(c); st.append(c)
r = w = rc = wc = 0
for pid in pids:
    try:
        for line in open('/proc/%d/io' % pid):
            k, v = line.split(':'); v = int(v)
            if k == 'rchar': r += v
            elif k == 'wchar': w += v
            elif k == 'syscr': rc += v
            elif k == 'syscw': wc += v
    except OSError: pass
print("pids=%d rchar=%d wchar=%d syscr=%d syscw=%d" % (len(pids), r, w, rc, wc))
