#!/usr/bin/env python3
"""bob.py - laptop side of the pendulum IMU bob.

Control the board over Wi-Fi, record its live stream, and turn any log (SD file or stream
recording) into CSV files on the ESP32 crystal time axis.

  export BOB_HOST=<hostname>.local   # or the IP; ESP_HOSTNAME from secret.h + ".local"
  export BOB_TOKEN=<OTA_TOKEN>       # from secret.h

  python bob.py status
  python bob.py check                       # mode 1: live sensor table (prints the Wi-Fi log)
  python bob.py selftest                    # mode 2: ODR-trigger lock test, prints the result
  python bob.py record run1.bin --label run1 --seconds 120   # run + live stream + convert
  python bob.py run start --label run1      # SD only (no laptop needed during the run)
  python bob.py mark release                # note in the log while running
  python bob.py run stop
  python bob.py flicker --hz 2000           # LEDs for the rolling-shutter calibration video
  python bob.py idle
  python bob.py stream run1.bin             # just listen/record (run started elsewhere)
  python bob.py convert run_001.bin -o run1/ [--imu-sensor 13]
  python bob.py log                         # print the board's text log
  python bob.py ota .pio/build/mbs/firmware.bin
  python bob.py mode run --save             # power-up mode (also: idle/check/selftest/flicker)

Only the standard library is needed, plus numpy for `convert` / `record`.
"""
import argparse
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# ------------------------------------------------------------------ log format (src/rec.h)
REC_FIFO, REC_SYNC, REC_STATUS, REC_NOTE, REC_LED, REC_DRDY, REC_ARM, REC_CONFIG, REC_FIFOST = range(1, 10)
REC_NAMES = {1: "fifo", 2: "sync", 3: "status", 4: "note", 5: "led", 6: "drdy", 7: "arm", 8: "config", 9: "fifost"}
HDR = struct.Struct("<BBHIq")           # rec_hdr_t
STATUS = struct.Struct("<HHIIbBH")      # rec_status_t
FILE_HDR_LEN = 16
PKT_HDR_LEN = 8
NUM_IMU = 13
MODES = {"idle": 0, "check": 1, "selftest": 2, "run": 3, "flicker": 4}

TAG_GY, TAG_XL, TAG_TS = 0x01, 0x02, 0x04
XL_MG_PER_LSB = {0: 0.061, 1: 0.122, 2: 0.244, 3: 0.488}
G_MDPS_PER_LSB = {0: 4.375, 1: 8.75, 2: 17.5, 3: 35.0, 4: 70.0, 12: 140.0}
TS_DEC_SLOTS = {0: 0, 1: 1, 2: 8, 3: 32}


def parse_file_header(b):
    if len(b) < FILE_HDR_LEN or b[:4] != b"PIMU":
        raise ValueError("not a PIMU log (bad magic)")
    h = dict(version=b[4], num_imu=b[5], ref_hz=b[6], nodr=b[7], fs_xl=b[8], fs_g=b[9],
             odr=b[10], bdr=b[11], haodr=b[12], ts_dec=b[13])
    if h["version"] < 2:
        raise ValueError("log format v%d: written by the old firmware, not supported" % h["version"])
    return h


def iter_records(buf, off=0):
    """Yield (type, sensor, seq, t_us, payload) from a bytes buffer of records."""
    n = len(buf)
    while off + HDR.size <= n:
        typ, sensor, nbytes, seq, t = HDR.unpack_from(buf, off)
        if typ not in REC_NAMES or nbytes > 4096:
            raise ValueError("corrupt record at byte %d (type %d, %d bytes)" % (off, typ, nbytes))
        end = off + HDR.size + nbytes
        if end > n:
            return                       # truncated tail (power cut mid-write): ignore
        yield typ, sensor, seq, t, buf[off + HDR.size:end]
        off = end


# ------------------------------------------------------------------ HTTP control
class Bob:
    def __init__(self, host, token, timeout=5.0):
        self.host = host
        self.token = token
        self.timeout = timeout

    def _url(self, path, params=None):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        return "http://%s%s%s" % (self.host, path, q)

    def get(self, path):
        with urllib.request.urlopen(self._url(path), timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def post(self, path, params=None, data=b"", timeout=None):
        req = urllib.request.Request(self._url(path, params), data=data, method="POST")
        if self.token:
            req.add_header("X-ota-Token", self.token)
        if data:
            req.add_header("Content-Type", "application/octet-stream")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise SystemExit("board said %d: %s" % (e.code, body))

    def wait_mode(self, name, timeout=20.0):
        t_end = time.time() + timeout
        while time.time() < t_end:
            try:
                st = self.get("/status")
                if st.get("mode_name") == name:
                    return st
            except OSError:
                pass
            time.sleep(0.3)
        st = self.get("/status")
        raise SystemExit("board did not reach mode '%s' (now '%s', last_error: %s)"
                         % (name, st.get("mode_name"), st.get("last_error")))


def local_ip_towards(host):
    """Our IP on the interface that routes to the board (for messages, not needed by the API)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((socket.gethostbyname(host.split(":")[0]), 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "?"


# ------------------------------------------------------------------ text log
def cmd_log(bob, args, seconds=None, stop_event=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", args.log_port))
    sock.settimeout(0.5)
    bob.post("/log", {"port": args.log_port})
    t_end = time.time() + seconds if seconds else None
    last_sub = time.time()
    try:
        while not (stop_event and stop_event.is_set()) and (t_end is None or time.time() < t_end):
            if time.time() - last_sub > 20:     # survives a board reboot
                try:
                    bob.post("/log", {"port": args.log_port})
                except (OSError, SystemExit):
                    pass
                last_sub = time.time()
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                continue
            sys.stdout.write(data.decode(errors="replace"))
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


# ------------------------------------------------------------------ live stream recorder
class StreamRecorder:
    """Receives UDP packets, writes a .bin identical in format to the SD file."""

    def __init__(self, path, port):
        self.path = path
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        self.sock.bind(("", port))
        self.sock.settimeout(0.2)
        self.f = None
        self.pending = []          # records received before the first CONFIG (no header yet)
        self.last_seq = None
        self.packets = 0
        self.lost = 0
        self.bytes = 0
        self.counts = {}
        self.last_status = None
        self.last_gyro = None      # U13 latest gyro (dps)
        self.fs_g = None
        self.notes = []

    def _write(self, raw):
        if self.f is None:
            self.pending.append(raw)
        else:
            self.f.write(raw)

    def handle_packet(self, data):
        if len(data) < PKT_HDR_LEN or data[:3] != b"PBS":
            return
        seq = struct.unpack_from("<I", data, 4)[0]
        if self.last_seq is not None:
            gap = (seq - self.last_seq - 1) & 0xFFFFFFFF
            if gap < 1 << 31:
                self.lost += gap
        self.last_seq = seq
        self.packets += 1
        self.bytes += len(data)
        off = PKT_HDR_LEN
        try:
            for typ, sensor, rseq, t, payload in iter_records(data, off):
                raw = HDR.pack(typ, sensor, len(payload), rseq, t) + payload
                self.counts[typ] = self.counts.get(typ, 0) + 1
                if typ == REC_CONFIG and self.f is None and len(payload) >= FILE_HDR_LEN:
                    hdr = parse_file_header(payload[:FILE_HDR_LEN])
                    self.fs_g = hdr["fs_g"]
                    self.f = open(self.path, "wb")
                    self.f.write(payload[:FILE_HDR_LEN])
                    for p in self.pending:
                        self.f.write(p)
                    self.pending = []
                self._write(raw)
                if typ == REC_STATUS and len(payload) >= STATUS.size:
                    self.last_status = STATUS.unpack_from(payload)
                elif typ == REC_NOTE:
                    self.notes.append(payload.decode(errors="replace"))
                elif typ == REC_FIFO and sensor == 12 and self.fs_g is not None:
                    for i in range(len(payload) - 7, -1, -7):
                        if payload[i] >> 3 == TAG_GY:
                            s = G_MDPS_PER_LSB.get(self.fs_g, 17.5) / 1000.0
                            self.last_gyro = [v * s for v in struct.unpack_from("<hhh", payload, i + 1)]
                            break
        except ValueError:
            pass

    def poll(self, until):
        while time.time() < until:
            try:
                data, _ = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            self.handle_packet(data)

    def summary_line(self, dt, prev_counts, prev_bytes):
        rate = {k: (v - prev_counts.get(k, 0)) / dt for k, v in self.counts.items()}
        st = self.last_status
        g = self.last_gyro
        s = "pkts %5d  lost %4d  %5.1f KB/s  fifo %4.0f/s  drdy %3.0f/s  sync %2.0f/s" % (
            self.packets, self.lost, (self.bytes - prev_bytes) / 1024.0 / dt,
            rate.get(REC_FIFO, 0), rate.get(REC_DRDY, 0), rate.get(REC_SYNC, 0))
        if st:
            s += "  vbat %4d mV  sd_drops %d  esp_stream_drops %d  fifo_ovr %d" % (st[0], st[2], st[3], st[6])
        if g:
            s += "  U13 gyro %7.1f %7.1f %7.1f dps" % tuple(g)
        return s

    def close(self):
        if self.f is None and self.pending:
            # never saw a CONFIG: write a header anyway so the file is readable
            self.f = open(self.path, "wb")
            self.f.write(b"PIMU" + bytes([2, NUM_IMU, 10, 10, 1, 2, 7, 7, 2, 2, 0, 0]))
            for p in self.pending:
                self.f.write(p)
        if self.f:
            self.f.close()
        self.sock.close()


def record_stream(bob, path, port, seconds=None, stop_event=None, on_start=None):
    rec = StreamRecorder(path, port)
    bob.post("/stream", {"port": port})
    print("listening on UDP %d (laptop %s), writing %s. Ctrl-C to stop." % (port, local_ip_towards(bob.host), path))
    if on_start:
        on_start()
    t0 = time.time()
    last_sub = t0
    last_print = t0
    prev_counts, prev_bytes = {}, 0
    try:
        while not (stop_event and stop_event.is_set()):
            now = time.time()
            if seconds and now - t0 >= seconds:
                break
            rec.poll(min(now + 0.25, last_print + 1.0))
            now = time.time()
            if now - last_sub > 15:              # re-subscribe: survives a board reboot
                try:
                    bob.post("/stream", {"port": port})
                except (OSError, SystemExit):
                    pass
                last_sub = now
            if now - last_print >= 1.0:
                dt = now - last_print
                print("[%6.1fs] %s" % (now - t0, rec.summary_line(dt, prev_counts, prev_bytes)))
                prev_counts, prev_bytes = dict(rec.counts), rec.bytes
                last_print = now
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        try:
            bob.post("/stream", {"port": 0})
        except (OSError, SystemExit):
            pass
        rec.poll(time.time() + 0.5)               # catch the tail
        rec.close()
    print("stream: %d packets, %d lost (%.2f %%), file %s" % (
        rec.packets, rec.lost, 100.0 * rec.lost / max(1, rec.packets + rec.lost), path))
    return rec


# ------------------------------------------------------------------ converter
def load_log(path):
    with open(path, "rb") as f:
        buf = f.read()
    hdr = parse_file_header(buf[:FILE_HDR_LEN])
    L = dict(hdr=hdr, fifo={i: [] for i in range(hdr["num_imu"])}, sync=[], drdy=[], led=[],
             arm={}, status=[], notes=[], config=[], fifost=[])
    warn = None
    try:
        for typ, sensor, seq, t, payload in iter_records(buf, FILE_HDR_LEN):
            if typ == REC_FIFO and sensor < hdr["num_imu"]:
                L["fifo"][sensor].append((seq, t, payload))
            elif typ == REC_SYNC:
                L["sync"].append((seq, t))
            elif typ == REC_DRDY:
                L["drdy"].append((seq, t))
            elif typ == REC_LED:
                L["led"].append((seq, sensor, t))
            elif typ == REC_ARM:
                L["arm"][sensor] = t
            elif typ == REC_STATUS and len(payload) >= STATUS.size:
                L["status"].append((t,) + STATUS.unpack_from(payload))
            elif typ == REC_NOTE:
                L["notes"].append((t, payload.decode(errors="replace")))
            elif typ == REC_CONFIG:
                L["config"].append((t, payload[FILE_HDR_LEN:].decode(errors="replace")))
            elif typ == REC_FIFOST:
                L["fifost"].append((sensor, seq, t))
    except ValueError as e:
        warn = str(e)
    L["warn"] = warn
    return L


class Slot:
    __slots__ = ("cnt", "ts", "xl", "g", "rec_i", "brk", "k")

    def __init__(self, cnt, rec_i, brk):
        self.cnt, self.rec_i, self.brk = cnt, rec_i, brk
        self.ts = self.xl = self.g = None
        self.k = None


def parse_sensor_fifo(recs):
    """recs: [(seq, t, payload)] -> slots, bursts.
    slot.brk = True when continuity with the previous slot is NOT guaranteed (lost record,
    TAG_CNT jump = FIFO overrun inside the sensor)."""
    recs = sorted({r[0]: r for r in recs}.values())          # sort by seq, drop duplicates
    slots, bursts = [], []
    cur = None
    pending_break = False
    for ri, (seq, t, payload) in enumerate(recs):
        if ri > 0 and seq != recs[ri - 1][0] + 1:
            pending_break = True
        for off in range(0, len(payload) - 6, 7):
            b0 = payload[off]
            tag, cnt = b0 >> 3, (b0 >> 1) & 3
            field = {TAG_TS: "ts", TAG_XL: "xl", TAG_GY: "g"}.get(tag)
            if field is None:
                continue
            new = cur is None or cnt != cur.cnt or getattr(cur, field) is not None or pending_break
            if new:
                brk = pending_break or (cur is not None and cnt != (cur.cnt + 1) % 4)
                cur = Slot(cnt, ri, brk)
                slots.append(cur)
                pending_break = False
            if field == "ts":
                cur.ts = struct.unpack_from("<I", payload, off + 1)[0]
            else:
                setattr(cur, field, struct.unpack_from("<hhh", payload, off + 1))
        # burst bookkeeping: a burst = consecutive records with the same t_us
        complete = ri + 1 < len(recs) and recs[ri + 1][0] == seq + 1 and recs[ri + 1][1] != t
        last_slot = len(slots) - 1
        if bursts and bursts[-1]["t"] == t and bursts[-1]["seq_end"] == seq - 1:
            bursts[-1].update(last=last_slot, seq_end=seq, complete=complete)
        else:
            bursts.append(dict(t=t, last=last_slot, seq_end=seq, complete=complete))
    return slots, bursts


def assign_slot_index(slots, ts_every):
    """Give every slot its index k (= sample number) using continuity and FIFO timestamps.
    Returns (step_ticks, n_dropped, n_segments)."""
    import numpy as np
    # segments of guaranteed-contiguous slots
    segs, cur = [], []
    for s in slots:
        if s.brk and cur:
            segs.append(cur)
            cur = []
        cur.append(s)
    if cur:
        segs.append(cur)

    # sensor timestamp ticks per slot, from TS pairs inside segments
    ratios = []
    for seg in segs:
        prev = None
        for pos, s in enumerate(seg):
            if s.ts is None:
                continue
            if prev is not None:
                d = (s.ts - prev[1]) & 0xFFFFFFFF
                ratios.append(d / (pos - prev[0]))
            prev = (pos, s.ts)
    if not ratios:
        # no timestamps at all: only the first segment can be placed
        for pos, s in enumerate(segs[0] if segs else []):
            s.k = pos
        return None, sum(len(sg) for sg in segs[1:]), len(segs)
    step = float(np.median(ratios))

    # split segments where TS spacing disagrees with slot counting (hidden overrun)
    fixed = []
    dropped = 0
    for seg in segs:
        part, last_ts = [], None
        for pos, s in enumerate(seg):
            if s.ts is not None and last_ts is not None:
                expect = (pos - last_ts[0])
                got = ((s.ts - last_ts[1]) & 0xFFFFFFFF) / step
                if abs(got - expect) > 0.3:
                    # keep slots up to the last good TS, drop the ambiguous ones, restart here
                    keep = last_ts[2] + 1
                    dropped += len(part) - keep
                    fixed.append(part[:keep])
                    part = []
            part.append(s)
            if s.ts is not None:
                last_ts = (pos, s.ts, len(part) - 1)
        if part:
            fixed.append(part)
    segs = [sg for sg in fixed if sg]

    # place segments: the first segment that has a timestamp starts at k = 0 (earlier ones
    # can't be related to it and are dropped); later ones are placed via their first TS
    anchor = None                 # (k, ts) of the latest placed TS
    local_steps = []
    for si, seg in enumerate(segs):
        first_ts = next((i for i, s in enumerate(seg) if s.ts is not None), None)
        if first_ts is None:
            dropped += len(seg)
            continue
        if anchor is None:
            base = 0
        else:
            st = float(np.median(local_steps[-16:])) if local_steps else step
            d = (seg[first_ts].ts - anchor[1]) & 0xFFFFFFFF
            base = anchor[0] + int(round(d / st)) - first_ts
        for i, s in enumerate(seg):
            s.k = base + i
            if s.ts is not None:
                if anchor is not None and s.k > anchor[0]:
                    local_steps.append(((s.ts - anchor[1]) & 0xFFFFFFFF) / (s.k - anchor[0]))
                anchor = (s.k, s.ts)
    # drop anything before the first placed slot keeps k >= 0 relative; shift so min k = 0
    ks = [s.k for s in slots if s.k is not None]
    if ks:
        m = min(ks)
        for s in slots:
            if s.k is not None:
                s.k -= m
    return step, dropped, len(segs)


def fit_grid(edges, period_us):
    """Least-squares t = A + B*j over (index, t) edges, j from time. Robust to ISR outliers."""
    import numpy as np
    t = np.array([e[1] for e in edges], dtype=float)
    t.sort()
    j = np.round((t - t[0]) / period_us)
    for _ in range(3):
        B, A = np.polyfit(j, t, 1)
        res = t - (A + B * j)
        mad = np.median(np.abs(res - np.median(res))) + 0.5
        keep = np.abs(res - np.median(res)) < 8 * mad
        if keep.all():
            break
        j, t = j[keep], t[keep]
    res = t - (A + B * j)
    return A, B, float(np.sqrt(np.mean(res ** 2))), float(np.max(np.abs(res))), len(t)


def solve_phase(slots, bursts, A, B, eps):
    """Integer m such that slot k happened at A + B*(k + m). Returns (candidates, slack_lo, slack_hi)."""
    import math
    lo, hi = -math.inf, math.inf
    for b in bursts:
        if b["last"] < 0:
            continue
        s = slots[b["last"]]
        if s.k is None:
            continue
        x = (b["t"] - A) / B
        hi = min(hi, (b["t"] + eps - A) / B - s.k)            # slot existed at the status read
        if b["complete"]:
            lo = max(lo, (b["t"] - eps - A) / B - s.k - 1)    # the next slot did not exist yet
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return [], lo, hi
    cands = list(range(math.floor(lo) + 1, math.floor(hi) + 1))
    return cands, lo, hi


def convert(path, outdir=None, imu_sensor=13, eps_us=300.0, quiet=False):
    import numpy as np
    L = load_log(path)
    h = L["hdr"]
    outdir = outdir or os.path.splitext(path)[0]
    os.makedirs(outdir, exist_ok=True)
    rep = []

    def say(s=""):
        rep.append(s)
        if not quiet:
            print(s)

    period = 1e6 / (h["ref_hz"] * 2 * h["nodr"])      # sample period, ESP clock (us)
    ts_every = TS_DEC_SLOTS.get(h["ts_dec"], 8)
    xl_s = XL_MG_PER_LSB.get(h["fs_xl"], 0.122) / 1000.0
    g_s = G_MDPS_PER_LSB.get(h["fs_g"], 17.5) / 1000.0
    say("log %s: format v%d, ref %d Hz x %d = %.3f Hz, fs_xl code %d, fs_g code %d, ts every %d samples"
        % (os.path.basename(path), h["version"], h["ref_hz"], 2 * h["nodr"], 1e6 / period, h["fs_xl"], h["fs_g"], ts_every))
    if L["warn"]:
        say("WARNING: %s (file read up to there)" % L["warn"])
    for t, txt in L["config"][:1]:
        say("config: " + txt)

    # ---- sample grid on the ESP clock
    grid_src = None
    if len(L["drdy"]) >= 20:
        A, B, rms, mx, n = fit_grid(L["drdy"], period)
        grid_src = "U13 DRDY"
        say("grid from %d U13 data-ready pulses: period %.4f us (%+.1f ppm vs %.1f), residual rms %.2f us, max %.1f us"
            % (n, B, (B / period - 1) * 1e6, period, rms, mx))
        if abs(B / period - 1) > 200e-6:
            say("WARNING: sample period differs from nominal by >200 ppm: the sensors are probably NOT locked "
                "to IMU_SYNC (run the selftest)")
    elif len(L["sync"]) >= 3:
        A0, Bref, rms, mx, n = fit_grid(L["sync"], 1e6 / h["ref_hz"])
        B = Bref / (2 * h["nodr"])
        A = A0
        grid_src = "IMU_SYNC edges"
        say("WARNING: no U13 DRDY pulses: grid from %d sync edges, sample phase assumed 0 us after the edge "
            "(absolute times may be off by up to one sample period; relative timing between sensors is still right)" % n)
    else:
        raise SystemExit("no DRDY or SYNC edges in the log: cannot build a time axis")
    if L["sync"]:
        sA, sB, srms, smx, sn = fit_grid(L["sync"], 1e6 / h["ref_hz"])
        say("IMU_SYNC: %d edges, period %.3f us (%+.1f ppm), jitter rms %.2f us" % (sn, sB, (sB * h["ref_hz"] / 1e6 - 1) * 1e6, srms))
        if grid_src == "U13 DRDY":
            phase = ((A - sA) % B)
            say("sample phase after each sync edge: %.1f us (mod one sample period)" % phase)

    # ---- per sensor: slots -> k -> m -> times
    per = {}
    for i in range(h["num_imu"]):
        if not L["fifo"][i]:
            continue
        slots, bursts = parse_sensor_fifo(L["fifo"][i])
        step, dropped, nseg = assign_slot_index(slots, ts_every)
        cands, lo, hi = solve_phase(slots, bursts, A, B, eps_us)
        per[i] = dict(slots=slots, bursts=bursts, step=step, dropped=dropped, nseg=nseg, cands=cands, lo=lo, hi=hi)

    # choose m: unique candidates directly; ambiguous ones take the common value
    unique = [p["cands"][0] for p in per.values() if len(p["cands"]) == 1]
    common = max(set(unique), key=unique.count) if unique else None
    say("")
    say(" sensor | samples | segments | dropped | ts step (ppm) | phase m | window slack (us) | note")
    for i, p in sorted(per.items()):
        c = p["cands"]
        note = ""
        if len(c) == 1:
            m = c[0]
        elif len(c) > 1:
            m = common if common in c else c[len(c) // 2]
            note = "ambiguous %s -> %d" % (c, m)
        else:
            m = common if common is not None else 0
            note = "INCONSISTENT window (lo %.2f hi %.2f): used %s" % (p["lo"], p["hi"], m)
        if common is not None and m != common and not note:
            note = "starts %+d samples vs others" % (m - common)
        p["m"] = m
        n_samples = sum(1 for s in p["slots"] if s.k is not None)
        ppm = ""
        if p["step"]:
            ppm = "%.2f (%+.0f)" % (p["step"], (p["step"] * 21.75 / period - 1) * 1e6)
        sl = "%.0f / %.0f" % (((p["hi"] - m) * B) if c else float("nan"), ((m - p["lo"]) * B) if c else float("nan"))
        say(" U%-5d | %7d | %8d | %7d | %13s | %7d | %17s | %s" % (i + 1, n_samples, p["nseg"], p["dropped"], ppm, m, sl, note))

    # ---- write CSVs
    rows = []
    for i, p in sorted(per.items()):
        for s in p["slots"]:
            if s.k is None or (s.xl is None and s.g is None):
                continue
            t_us = A + B * (s.k + p["m"])
            rows.append((t_us, i + 1, s.k, s.xl, s.g))
    rows.sort(key=lambda r: (r[0], r[1]))
    f_all = os.path.join(outdir, "imu_all.csv")
    with open(f_all, "w") as f:
        f.write("t_s,sensor,sample,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps\n")
        for t_us, sid, k, xl, g in rows:
            a = ",".join("%.5f" % (v * xl_s) for v in xl) if xl else ",,"
            w = ",".join("%.4f" % (v * g_s) for v in g) if g else ",,"
            f.write("%.6f,%d,%d,%s,%s\n" % (t_us / 1e6, sid, k, a, w))
    n_imu = 0
    with open(os.path.join(outdir, "imu.csv"), "w") as f:      # vision pipeline format
        f.write("t_s,gx,gy,gz\n")
        for t_us, sid, k, xl, g in rows:
            if sid == imu_sensor and g:
                f.write("%.6f,%.4f,%.4f,%.4f\n" % (t_us / 1e6, g[0] * g_s, g[1] * g_s, g[2] * g_s))
                n_imu += 1
    with open(os.path.join(outdir, "led_edges.csv"), "w") as f:
        f.write("t_us,state\n")
        for idx, state, t in sorted(L["led"], key=lambda r: r[2]):
            f.write("%d,%d\n" % (t, state))
    with open(os.path.join(outdir, "sync_edges.csv"), "w") as f:
        f.write("index,t_us\n")
        for idx, t in sorted(L["sync"]):
            f.write("%d,%d\n" % (idx, t))
    with open(os.path.join(outdir, "status.csv"), "w") as f:
        f.write("t_s,vbat_mv,vbus_mv,sd_drops,esp_stream_drops,rssi_dbm,fifo_ovr\n")
        for t, vbat, vbus, sdd, std, rssi, mode, ovr in L["status"]:
            f.write("%.3f,%d,%d,%d,%d,%d,%d\n" % (t / 1e6, vbat, vbus, sdd, std, rssi, ovr))
    with open(os.path.join(outdir, "notes.csv"), "w") as f:
        f.write("t_s,text\n")
        for t, txt in L["notes"]:
            f.write('%.6f,"%s"\n' % (t / 1e6, txt.replace('"', "'")))
    # numpy bundle for analysis
    if rows:
        t = np.array([r[0] for r in rows]) / 1e6
        sid = np.array([r[1] for r in rows], dtype=np.int16)
        acc = np.array([[v * xl_s for v in r[3]] if r[3] else [np.nan] * 3 for r in rows])
        gyr = np.array([[v * g_s for v in r[4]] if r[4] else [np.nan] * 3 for r in rows])
        np.savez_compressed(os.path.join(outdir, "imu_all.npz"), t_s=t, sensor=sid, acc_g=acc, gyro_dps=gyr)

    say("")
    say("LED edges: %d   sync edges: %d   notes: %d   FIFO overruns flagged: %d"
        % (len(L["led"]), len(L["sync"]), len(L["notes"]), len(L["fifost"])))
    if L["status"]:
        last = L["status"][-1]
        say("ESP-side drops at end: SD %d, stream %d" % (last[3], last[4]))
    for t, txt in L["notes"]:
        say("note %10.3f s: %s" % (t / 1e6, txt))
    say("wrote %s/{imu_all.csv, imu_all.npz, imu.csv (U%d, %d rows), led_edges.csv, sync_edges.csv, status.csv, notes.csv}"
        % (outdir, imu_sensor, n_imu))
    with open(os.path.join(outdir, "timing_report.txt"), "w") as f:
        f.write("\n".join(rep) + "\n")
    return dict(A=A, B=B, per=per, rows=rows, outdir=outdir)


# ------------------------------------------------------------------ commands
def print_status(st):
    run = st.get("run", {})
    print("fw %s (%s)  mode %s  boot-mode %s  sensors %s" % (st.get("fw"), st.get("partition"), st.get("mode_name"),
                                                          st.get("boot_mode"), st.get("sensors_ok")))
    print("battery %s mV  usb %s mV  rssi %s dBm  heap %s B  uptime %s s" % (
        st.get("vbat_mv"), st.get("vbus_mv"), st.get("rssi_dbm"), st.get("free_heap"), st.get("uptime_s")))
    if st.get("running"):
        print("RUN %s  label %s  mask %s  t=%s s  records %s  sd_drops %s  fifo_ovr %s" % (
            run.get("file") or "(no SD)", run.get("label"), run.get("mask"), run.get("t_s"), run.get("records"),
            run.get("sd_drops"), run.get("fifo_ovr")))
    s = st.get("stream", {})
    print("stream active %s  packets %s  drops %s" % (s.get("active"), s.get("packets"), s.get("drops")))
    if st.get("last_error"):
        print("last error: %s" % st["last_error"])


def print_selftest(r):
    print("selftest: %s  edges %s in %s s  interval %s ms  expected %s samples/edge" % (
        "PASS" if r.get("ok") else "FAIL", r.get("edges"), r.get("secs"), r.get("interval_ms"), r.get("expected")))
    for s in r.get("sensors", []):
        if s.get("present"):
            print("  %-4s xl/edge %6.2f  gyro/edge %6.2f  ovr %d  %s" % (
                s["id"], s["xl_per_edge"], s["gy_per_edge"], s["ovr"], "ok" if s["ok"] else "NO"))
        else:
            print("  %-4s no response" % s["id"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("MBS_HOST", "mbs.local"))
    ap.add_argument("--token", default=os.environ.get("MBS_TOKEN", ""))
    ap.add_argument("--log-port", type=int, default=5006)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("mode"); p.add_argument("name", choices=list(MODES)); p.add_argument("--save", action="store_true")
    p.add_argument("--hz", type=int); p.add_argument("--mask"); p.add_argument("--no-sd", action="store_true"); p.add_argument("--label")
    sub.add_parser("idle")
    p = sub.add_parser("check"); p.add_argument("--seconds", type=float, default=0)
    sub.add_parser("selftest")
    p = sub.add_parser("run"); p.add_argument("action", choices=["start", "stop"])
    p.add_argument("--mask", default="all"); p.add_argument("--no-sd", action="store_true"); p.add_argument("--label", default="")
    p = sub.add_parser("mark"); p.add_argument("text", nargs="+")
    p = sub.add_parser("flicker"); p.add_argument("--hz", type=int, default=2000)
    p = sub.add_parser("stream"); p.add_argument("out"); p.add_argument("--port", type=int, default=5005)
    p.add_argument("--seconds", type=float)
    p = sub.add_parser("record", help="start a run, record the live stream, stop, convert")
    p.add_argument("out"); p.add_argument("--port", type=int, default=5005); p.add_argument("--seconds", type=float)
    p.add_argument("--mask", default="all"); p.add_argument("--no-sd", action="store_true"); p.add_argument("--label", default="")
    p.add_argument("--imu-sensor", type=int, default=13); p.add_argument("--no-convert", action="store_true")
    p = sub.add_parser("convert"); p.add_argument("log"); p.add_argument("-o", "--outdir")
    p.add_argument("--imu-sensor", type=int, default=13, help="sensor for imu.csv (1..13, default 13 = centre)")
    p.add_argument("--eps-us", type=float, default=300.0)
    sub.add_parser("log")
    p = sub.add_parser("ota"); p.add_argument("bin")
    args = ap.parse_args()

    if args.cmd == "convert":
        convert(args.log, args.outdir, args.imu_sensor, args.eps_us)
        return
    bob = Bob(args.host, args.token)
    need_token = args.cmd not in ("status",)
    if need_token and not args.token:
        raise SystemExit("set MBS_TOKEN (OTA_TOKEN from secret.h) or pass --token")

    if args.cmd == "status":
        print_status(bob.get("/status"))
    elif args.cmd in ("mode", "idle"):
        name = "idle" if args.cmd == "idle" else args.name
        prm = {"n": MODES[name]}
        if args.cmd == "mode":
            if args.save: prm["save"] = 1
            if args.hz: prm["hz"] = args.hz
            if args.mask: prm["mask"] = args.mask
            if args.no_sd: prm["sd"] = 0
            if args.label: prm["label"] = args.label
        print(bob.post("/mode", prm))
    elif args.cmd == "check":
        bob.post("/mode", {"n": 1})
        print("mode check: printing the board's table (Ctrl-C to stop; board stays in check mode)")
        cmd_log(bob, args, seconds=args.seconds or None)
    elif args.cmd == "selftest":
        bob.post("/mode", {"n": 2})
        print("selftest running (~12 s)...")
        time.sleep(3)
        t_end = time.time() + 30
        while time.time() < t_end:
            r = bob.get("/selftest")
            if r.get("done"):
                print_selftest(r)
                return
            time.sleep(1)
        raise SystemExit("no selftest result after 30 s; check `bob.py status`")
    elif args.cmd == "run":
        if args.action == "start":
            prm = {"mask": args.mask, "sd": 0 if args.no_sd else 1}
            if args.label: prm["label"] = args.label
            bob.post("/run/start", prm)
            print_status(bob.wait_mode("run"))
        else:
            bob.post("/run/stop")
            st = bob.wait_mode("idle")
            print("stopped; SD file closed. %s" % (st.get("last_error") or ""))
    elif args.cmd == "mark":
        print(bob.post("/mark", {"text": " ".join(args.text)}))
    elif args.cmd == "flicker":
        print(bob.post("/flicker", {"hz": args.hz}))
    elif args.cmd == "stream":
        record_stream(bob, args.out, args.port, args.seconds)
    elif args.cmd == "record":
        def start():
            prm = {"mask": args.mask, "sd": 0 if args.no_sd else 1}
            if args.label: prm["label"] = args.label
            bob.post("/run/start", prm)
            st = bob.wait_mode("run")
            print("run started: %s" % (st["run"].get("file") or "stream only"))
        try:
            record_stream(bob, args.out, args.port, args.seconds, on_start=start)
        finally:
            try:
                bob.post("/run/stop")
                bob.wait_mode("idle")
                print("run stopped, SD file closed")
            except SystemExit as e:
                print("stop: %s" % e)
        if not args.no_convert:
            convert(args.out, None, args.imu_sensor)
    elif args.cmd == "log":
        cmd_log(bob, args)
    elif args.cmd == "ota":
        data = open(args.bin, "rb").read()
        print("uploading %d bytes..." % len(data))
        print(bob.post("/ota", data=data, timeout=120))
        print("rebooting; waiting for the board...")
        time.sleep(5)
        for _ in range(40):
            try:
                print_status(bob.get("/status"))
                return
            except OSError:
                time.sleep(1)
        print("board not back after 45 s (it may have rolled back; check USB serial)")


if __name__ == "__main__":
    main()
