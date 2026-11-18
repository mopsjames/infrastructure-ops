#!/usr/bin/env python3
"""fan-guard: turn Dell PowerEdge fans down where temperatures allow, and never louder than stock.

Every INTERVAL seconds it reads the inlet and CPU temperatures (IPMI), every drive's
temperature (smartctl) and, if present, an Nvidia GPU's. While everything is at least
STEP_DOWN_MARGIN degrees under its target, it lowers the fans by STEP_DOWN, at most once
every STEP_DOWN_EVERY intervals and never below MIN_PCT; when anything goes over its
target it raises them quickly. It hands control back to the iDRAC's automatic mode, and
waits RETRY_AFTER before trying again, when:
  - any sensor can't be read, or a reading is missing (fail safe);
  - anything reaches its hard limit;
  - cooling enough would need the fans faster than automatic mode was running them,
    so it never makes a host louder than stock.
The systemd unit's ExecStopPost also returns the fans to automatic if this script stops or dies.

Settings: optional JSON in /etc/fan-guard.json overrides the defaults below (see README).
State for monitoring: /run/fan-guard/state.json (mode, pct, reason, worst sensor, time).
Usage: fan-guard            run the loop (as root, from fan-guard.service)
       fan-guard --status   print the current readings and what it would do
Tested on Dell 13th-generation servers (R530, R730xd). Raw IPMI commands used:
  0x30 0x30 0x01 0x00 manual   0x30 0x30 0x01 0x01 automatic   0x30 0x30 0x02 0xff <pct> set all fans
"""
import json, os, re, subprocess, sys, time

CONFIG = '/etc/fan-guard.json'
DEFAULTS = {
    'interval': 60,          # seconds between readings
    'retry_after': 1800,     # seconds to stay on automatic after handing control back
    'start_pct': 30,         # first manual setting; the loop moves from there
    'min_pct': 15,           # lowest manual setting. Raise it if a drive heats up minutes after
                             # the fans slow (a rear flex-bay SSD in an R730xd needed 40)
    'watch_only': False,     # true: only read and report, never take the fans
    'step_down': 1,          # percent per step when there is room
    'step_down_every': 5,    # intervals between step-downs, so slow sensors catch up first
    'step_up': 6,            # percent per interval when something is over target
    'step_down_margin': 3,   # degrees under target needed before stepping down
    # Drives that report a SMART "Drive Trip Temperature" (most SAS drives; it differs by model,
    # e.g. 50, 60 or 64 C) get target = trip - gap and hard limit = trip - trip_hard_gap.
    'trip_target_gap': {'hdd': 9, 'ssd': 7},
    'trip_hard_gap': 4,
    # (target, hard limit) in C. Targets are where fan-guard aims to hold the hottest sensor of
    # each kind; at the hard limit it gives control back to the iDRAC at once.
    'limits': {
        'inlet': [27, 32],   # a hot room is not something to save fan noise on
        'cpu':   [65, 80],
        'hdd':   [45, 50],   # fallback for drives without a trip temperature
        'ssd':   [55, 60],   # fallback, as for hdd
        'gpu':   [75, 85],
    },
}
try:
    _user = json.load(open(CONFIG))
except FileNotFoundError:
    _user = {}
CFG = {**DEFAULTS, **_user}
INTERVAL, RETRY_AFTER, START_PCT, MIN_PCT = CFG['interval'], CFG['retry_after'], CFG['start_pct'], CFG['min_pct']
WATCH_ONLY = bool(CFG['watch_only'])
STEP_DOWN, STEP_DOWN_EVERY, STEP_UP, STEP_DOWN_MARGIN = (CFG['step_down'], CFG['step_down_every'],
                                                         CFG['step_up'], CFG['step_down_margin'])
TRIP_TARGET_GAP, TRIP_HARD_GAP = CFG['trip_target_gap'], CFG['trip_hard_gap']
LIMITS = {**DEFAULTS['limits'], **CFG.get('limits', {})}
STATE_DIR = '/run/fan-guard'


def run(cmd, timeout=20):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True).stdout


def ipmi(*args):
    return run(['ipmitool', *args])


def set_auto():
    ipmi('raw', '0x30', '0x30', '0x01', '0x01')


def set_manual(pct):
    ipmi('raw', '0x30', '0x30', '0x01', '0x00')
    ipmi('raw', '0x30', '0x30', '0x02', '0xff', hex(int(pct)))


def fan_rpms():
    rpms = []
    for line in ipmi('sdr', 'type', 'Fan').splitlines():
        m = re.search(r'\|\s*ok\s*\|.*\|\s*(\d+) RPM', line)
        if m:
            rpms.append(int(m.group(1)))
    if not rpms:
        raise RuntimeError('no fan readings')
    return sum(rpms) / len(rpms)


def readings():
    """Return [(kind, name, celsius, target, hard)]. Raises if anything expected can't be read."""
    out = []
    for line in ipmi('sdr', 'type', 'Temperature').splitlines():
        f = [x.strip() for x in line.split('|')]
        if len(f) < 5 or f[2] != 'ok':
            continue
        m = re.match(r'(-?\d+) degrees C', f[4])
        if not m:
            continue
        if f[0] == 'Inlet Temp':
            out.append(('inlet', 'inlet', int(m.group(1)), *LIMITS['inlet']))
        elif f[0] == 'Temp':                         # the CPU package sensors
            out.append(('cpu', f'cpu {f[3]}', int(m.group(1)), *LIMITS['cpu']))
    if not any(r[0] == 'inlet' for r in out) or not any(r[0] == 'cpu' for r in out):
        raise RuntimeError('inlet or CPU temperature missing')
    disks = [l.split() for l in run(['lsblk', '-dno', 'NAME,TYPE,ROTA']).splitlines()]
    for name, typ, rota in (d for d in disks if len(d) == 3):
        if typ != 'disk' or name.startswith('zd'):
            continue
        try:
            a = subprocess.run(['smartctl', '-A', f'/dev/{name}'], capture_output=True, text=True, timeout=20).stdout
        except subprocess.TimeoutExpired:
            raise RuntimeError(f'smartctl timed out on {name}')
        m = re.search(r'Current Drive Temperature:\s+(\d+)', a) or re.search(r'^194 .*\s(\d+)(?: \(.*\))?$', a, re.M)
        if not m:
            raise RuntimeError(f'no temperature for {name}')
        kind = 'hdd' if rota == '1' else 'ssd'
        trip = re.search(r'Drive Trip Temperature:\s+(\d+)', a)
        target, hard = ((int(trip.group(1)) - TRIP_TARGET_GAP[kind], int(trip.group(1)) - TRIP_HARD_GAP)
                        if trip else LIMITS[kind])
        out.append((kind, name, int(m.group(1)), target, hard))
    if os.path.exists('/usr/bin/nvidia-smi'):
        t = run(['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits']).split()
        if not t:
            raise RuntimeError('no GPU temperature')
        out.append(('gpu', 'gpu', int(t[0]), *LIMITS['gpu']))
    return out


def assess(rs):
    """(over_hard, margin, worst): margin is the smallest (target - temp) over all sensors."""
    over = [(k, n, t) for k, n, t, _, hard in rs if t >= hard]
    worst = min(rs, key=lambda r: r[3] - r[2])
    return over, worst[3] - worst[2], worst


def write_state(**kv):
    os.makedirs(STATE_DIR, exist_ok=True)
    kv['ts'] = int(time.time())
    tmp = f'{STATE_DIR}/state.json.tmp'
    with open(tmp, 'w') as f:
        json.dump(kv, f)
    os.replace(tmp, f'{STATE_DIR}/state.json')


def log(*words):
    print(*words, flush=True)


def status():
    rs = readings()
    over, margin, worst = assess(rs)
    for k, n, t, target, hard in sorted(rs):
        print(f'{k:5} {n:10} {t:3} C   target {target}, hard {hard}')
    print(f'fans {fan_rpms():.0f} RPM average; worst {worst[1]} margin {margin}; over hard: {over or "none"}')
    try:
        print('state:', open(f'{STATE_DIR}/state.json').read())
    except OSError:
        print('state: none (not running)')


def main():
    mode, pct, auto_rpm, retry_at, last_change = 'auto', None, None, 0, 0
    set_auto()
    log('started; fans on automatic' + (' (watch only on this host)' if WATCH_ONLY else ''))
    while True:
        reason = ''
        try:
            rs = readings()
            over, margin, worst = assess(rs)
            if mode == 'auto':
                auto_rpm = fan_rpms()                 # what stock control is doing right now
                if over:
                    reason = 'over hard limit: ' + ', '.join(f'{n} {t} C' for _, n, t in over)
                elif not WATCH_ONLY and time.time() >= retry_at and margin >= STEP_DOWN_MARGIN:
                    mode, pct, last_change = 'manual', max(MIN_PCT, START_PCT), time.time()
                    set_manual(pct)
                    log(f'manual at {pct}% (auto was {auto_rpm:.0f} RPM; worst {worst[1]} {worst[2]} C)')
            else:
                if over:
                    raise RuntimeError('over hard limit: ' + ', '.join(f'{n} {t} C' for _, n, t in over))
                if margin < 0:
                    new = min(100, pct + STEP_UP)
                elif margin >= STEP_DOWN_MARGIN and time.time() - last_change >= STEP_DOWN_EVERY * INTERVAL - 5:
                    new = max(MIN_PCT, pct - STEP_DOWN)
                else:
                    new = pct
                if new != pct:
                    set_manual(new)
                    log(f'{pct}% -> {new}% (worst {worst[1]} {worst[2]} C, margin {margin})')
                    pct, last_change = new, time.time()
                if margin < 0 and auto_rpm and fan_rpms() >= auto_rpm:
                    raise RuntimeError(f'needs more cooling than automatic gave ({auto_rpm:.0f} RPM)')
            write_state(mode=mode, pct=pct if mode == 'manual' else None, worst=f'{worst[1]} {worst[2]} C',
                        margin=margin, auto_rpm=round(auto_rpm or 0), reason=reason)
        except Exception as e:
            log('handing fans back to the iDRAC:', e)
            try:
                set_auto()
            except Exception as e2:
                log('could not set automatic:', e2)
            mode, pct, retry_at = 'auto', None, time.time() + RETRY_AFTER
            write_state(mode='auto', pct=None, worst='', margin=None, auto_rpm=0, reason=str(e)[:200])
        time.sleep(INTERVAL)


if __name__ == '__main__':
    if '--status' in sys.argv:
        status()
    else:
        try:
            main()
        finally:
            try:
                set_auto()
            except Exception:
                pass
