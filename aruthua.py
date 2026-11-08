#!/usr/bin/env python3
# ARUTHUA - HTTP Proxy for Termux (colorful dashboard, pure Python)
# Run: python3 aruthua.py        Stop: CTRL + C

import base64
import hashlib
import hmac
import json
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta, time as dtime
from urllib.parse import urlsplit

HOST = "0.0.0.0"
PORT = 8080
AUTH = None            # password ထည့်ချင်ရင် "user:pass" လို့ ပြောင်းပါ
BUF = 65536

stats = {"sent": 0, "recv": 0, "total_conn": 0, "active": 0}
session_log = deque(maxlen=50)
lock = threading.Lock()


# ───────────────────────── proxy core ─────────────────────────
def add(key, n):
    with lock:
        stats[key] += n


def tunnel(a, b):
    socks = [a, b]
    while True:
        r, _, x = select.select(socks, [], socks, 60)
        if x or not r:
            return
        for s in r:
            data = s.recv(BUF)
            if not data:
                return
            if s is a:
                b.sendall(data)
                add("sent", len(data))
            else:
                a.sendall(data)
                add("recv", len(data))


def handle(client, addr):
    remote = None
    with lock:
        stats["active"] += 1
        stats["total_conn"] += 1
    try:
        client.settimeout(30)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(BUF)
            if not chunk:
                return
            data += chunk
            if len(data) > 262144:
                return

        head, _, rest = data.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ", 2)
        headers = lines[1:]

        if AUTH:
            token = base64.b64encode(AUTH.encode()).decode()
            ok = any(
                h.lower().startswith("proxy-authorization:") and h.split(None, 2)[-1] == token
                for h in headers
            )
            if not ok:
                client.sendall(
                    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                    b'Proxy-Authenticate: Basic realm="proxy"\r\n'
                    b"Content-Length: 0\r\n\r\n"
                )
                return

        if method.upper() == "CONNECT":
            host, _, port = target.rpartition(":")
            session_log.append((time.strftime("%H:%M:%S"), "CONN", host))
            remote = socket.create_connection((host, int(port)), timeout=15)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                remote.sendall(rest)
                add("sent", len(rest))
        else:
            u = urlsplit(target)
            host = u.hostname
            port = u.port or 80
            path = (u.path or "/") + (("?" + u.query) if u.query else "")
            session_log.append((time.strftime("%H:%M:%S"), method.upper()[:4], host))
            remote = socket.create_connection((host, port), timeout=15)
            skip = ("proxy-connection:", "proxy-authorization:", "connection:")
            keep = [h for h in headers if h and not h.lower().startswith(skip)]
            req = f"{method} {path} {version}\r\n" + "\r\n".join(keep) + "\r\nConnection: close\r\n\r\n"
            out = req.encode("latin-1") + rest
            remote.sendall(out)
            add("sent", len(out))

        client.settimeout(None)
        remote.settimeout(None)
        tunnel(client, remote)
    except Exception:
        pass
    finally:
        for s in (client, remote):
            try:
                if s:
                    s.close()
            except Exception:
                pass
        with lock:
            stats["active"] -= 1


def accept_loop(server):
    while True:
        try:
            c, addr = server.accept()
        except OSError:
            return
        threading.Thread(target=handle, args=(c, addr), daemon=True).start()


def get_ips():
    ips = []
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
        ips = [i for i in re.findall(r"inet (?:addr:)?(\d+\.\d+\.\d+\.\d+)", out) if not i.startswith("127.")]
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips = [s.getsockname()[0]]
            s.close()
        except Exception:
            pass
    return ips


# ───────────────────────── UI helpers ─────────────────────────
RST = "\033[0m"
ANSI_RE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")


def c(code, text, bold=False):
    return f"\033[{'1;' if bold else ''}38;5;{code}m{text}{RST}"


def dim(text):
    return c(244, text)


def vlen(s):
    return len(ANSI_RE.sub("", s))


def pad(s, n):
    return s + " " * max(0, n - vlen(s))


def fmt_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_bps(n):
    n = float(n)
    for unit in ("bps", "Kbps", "Mbps"):
        if n < 1000:
            return f"{int(n)} bps" if unit == "bps" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} Gbps"


BARS = "▁▂▃▄▅▆▇█"


def spark(vals, n):
    vals = list(vals)[-n:]
    vals = [0] * (n - len(vals)) + vals
    m = max(vals) or 1
    return "".join(BARS[min(7, int(v / m * 7))] if v > 0 else BARS[0] for v in vals)


LOGO = [
    ("▄▀█", "█▀█"),  # A
    ("█▀█", "█▀▄"),  # R
    ("█ █", "█▄█"),  # U
    ("▀█▀", " █ "),  # T
    ("█ █", "█▀█"),  # H
    ("█ █", "█▄█"),  # U
    ("▄▀█", "█▀█"),  # A
]
LOGO_BIG = {
    "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
    "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    "U": ["█   █", "█   █", "█   █", "█   █", " ███ "],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
}
WORD = "ARUTHUA"
GRAD = [51, 45, 39, 33, 99, 135, 171]


def center(s, w):
    return " " * max(0, (w - vlen(s)) // 2) + s


def box(title, rows, w, color=39):
    inner = w - 4
    top = c(color, "┌─ ") + c(color, title, True) + c(color, " " + "─" * (w - 5 - len(title)) + "┐")
    bot = c(color, "└" + "─" * (w - 2) + "┘")
    body = [c(color, "│") + " " + pad(r, inner) + " " + c(color, "│") for r in rows]
    return [top] + body + [bot]


def logo_lines(w):
    L = []
    if w >= 42:
        for row in range(5):
            line = " ".join(c(GRAD[i], LOGO_BIG[ch][row], True) for i, ch in enumerate(WORD))
            L.append(center(line, w))
    else:
        for row in (0, 1):
            line = " ".join(c(GRAD[i], LOGO[i][row], True) for i in range(len(LOGO)))
            L.append(center(line, w))
    L.append("")
    L.append(center(dim("─── ") + c(250, "HTTP PROXY SERVER", True) + dim(" ───"), w))
    L.append(center(c(213, "Owens ", True) + c(51, "@aruthua", True), w))
    L.append("")
    return L


def render(w, ips, cur_s, cur_r, max_s, max_r, hist, start, lic):
    inner = w - 4
    L = []

    L += logo_lines(w)

    # status
    up = int(time.time() - start)
    uptime = f"{up // 3600:02d}:{up % 3600 // 60:02d}:{up % 60:02d}"
    dot = c(82, "●", True) if int(time.time()) % 2 == 0 else c(34, "●")
    rem = max(0, lic["end"] - time.time())
    d, h, m = int(rem // 86400), int(rem % 86400 // 3600), int(rem % 3600 // 60)
    left = f"{d}d {h}h {m}m" if d else f"{h}h {m}m"
    lcol = 82 if rem > 3 * 86400 else (226 if rem > 86400 else 196)
    L += box("STATUS", [
        dot + " " + c(82, "RUNNING", True) + dim("   Uptime ") + c(255, uptime),
        dim("Device   ") + c(255, lic["dev"]),
        dim("Expires  ") + c(lcol, fmt_dt(lic["end"]), True),
        dim("Left     ") + c(lcol, left),
    ], w, 82)

    # proxy details
    auth = "None" if not AUTH else AUTH.split(":")[0] + ":***"
    L += box("PROXY DETAILS", [
        dim("Host  ") + c(229, HOST),
        dim("Port  ") + c(229, str(PORT)),
        dim("Auth  ") + c(229, auth),
    ], w, 214)

    # ip
    rows = []
    for n, ip in enumerate(ips):
        if n:
            rows.append(dim("┄" * inner))
        rows.append(dim("IP    ") + c(117, ip, True))
        rows.append(dim("Port  ") + c(229, str(PORT), True))
    if not rows:
        rows = [dim("IP မတွေ့ပါ")]
    L += box("IP ADDRESSES", rows, w, 117)

    # traffic
    with lock:
        tot_s, tot_r = stats["sent"], stats["recv"]
    L += box("TRAFFIC", [
        c(208, "▲ Sent  ", True) + dim("Now ") + pad(c(255, fmt_bps(cur_s)), 11) + dim("Max ") + c(255, fmt_bps(max_s)),
        "        " + dim("Total ") + c(208, fmt_bytes(tot_s)),
        c(82, "▼ Recv  ", True) + dim("Now ") + pad(c(255, fmt_bps(cur_r)), 11) + dim("Max ") + c(255, fmt_bps(max_r)),
        "        " + dim("Total ") + c(82, fmt_bytes(tot_r)),
        c(82, spark(hist, inner)),
    ], w, 141)

    # connections
    with lock:
        act, tot = stats["active"], stats["total_conn"]
    L += box("CONNECTIONS", [
        dim("Active ") + c(226, str(act), True) + dim("    Total ") + c(255, str(tot)),
    ], w, 220)

    # session log
    logs = list(session_log)[-5:]
    rows = []
    mcol = {"CONN": 226, "GET": 82, "POST": 208}
    for t, m, h in logs:
        avail = inner - 9 - 5
        rows.append(dim(t) + " " + c(mcol.get(m, 250), f"{m:<4}", True) + " " + c(250, h[:avail]))
    L += box("SESSION LOG", rows or [dim("waiting for connections...")], w, 75)

    L.append("")
    L.append(center(dim("CTRL + C နှိပ်ရင် ရပ်မယ်"), w))
    return L


# ───────────────────────── license ─────────────────────────
_SECRET = b"666d13e70e703b449557daa5813f5e3f"
EPOCH = date(2020, 1, 1)
ACTIVATE_WINDOW = 3        # key ထုတ်တဲ့နေ့ကစပြီး ရက် ၃ ရက်အတွင်း ထည့်ရမယ်
LIC_FILE = os.path.expanduser("~/.aruthua_license")
ID_FILE = os.path.expanduser("~/.aruthua_id")


def _sign(dev, payload):
    msg = dev + "|" + json.dumps(payload, sort_keys=True)
    return hmac.new(_SECRET, msg.encode(), hashlib.sha256).hexdigest()


def fmt_dt(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def device_id():
    """ဖုန်းတစ်လုံးစီ မတူတဲ့ ID (ဂဏန်း ၁၀ လုံး)"""
    try:
        with open(ID_FILE) as f:
            v = f.read().strip()
        if re.fullmatch(r"\d{8,10}", v):
            return v
    except Exception:
        pass
    raw = ""
    try:
        raw = subprocess.run(["settings", "get", "secure", "android_id"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        pass
    if re.fullmatch(r"[0-9a-f]{16}", raw):
        n = int(hashlib.sha256(("aruthua:" + raw).encode()).hexdigest(), 16)
    else:
        n = int.from_bytes(os.urandom(8), "big")
    v = str(n % 9000000000 + 1000000000)
    try:
        with open(ID_FILE, "w") as f:
            f.write(v)
    except Exception:
        pass
    return v


def load_license(dev):
    try:
        with open(LIC_FILE) as f:
            d = json.load(f)
        sig = d.pop("sig", "")
        if hmac.compare_digest(sig, _sign(dev, d)):
            return d
    except Exception:
        pass
    return {}


def save_license(dev, d):
    body = {k: v for k, v in d.items() if k != "sig"}
    out = dict(body)
    out["sig"] = _sign(dev, body)
    try:
        with open(LIC_FILE, "w") as f:
            json.dump(out, f)
    except Exception:
        pass


def touch_license(lic, now):
    lic["st"]["last"] = now
    save_license(lic["dev"], lic["st"])


def parse_key(key, dev):
    """return ((issue_day, days, key_id), error)"""
    k = re.sub(r"[^A-Z2-7]", "", key.upper())
    if len(k) != 24:
        return None, "Invalid key format"
    try:
        raw = base64.b32decode(k)
    except Exception:
        return None, "Invalid key format"
    issue = int.from_bytes(raw[:2], "big")
    days = int.from_bytes(raw[2:4], "big")
    tag = hmac.new(_SECRET, f"{dev}:{issue}:{days}".encode(), hashlib.sha256).digest()[:11]
    if days < 1 or not hmac.compare_digest(raw[4:], tag):
        return None, "Key is not valid for this device"
    return (issue, days, hashlib.sha256(k.encode()).hexdigest()[:16]), None


def activate(key, dev, st):
    """return (new_state, error)"""
    parsed, err = parse_key(key, dev)
    if err:
        return None, err
    issue, days, kid = parsed
    if kid in st.get("used", []):
        return None, "This key was already used"
    today = (date.today() - EPOCH).days
    if today < issue - 1:
        return None, "Phone date is wrong. Set the correct date."
    if today > issue + ACTIVATE_WINDOW:
        return None, "This key is too old. Ask the owner for a new key."
    now = time.time()
    if now < st.get("last", 0) - 600:
        return None, "Phone date/time was changed. Set the correct time."
    return {
        "start": now,
        "end": now + days * 86400,
        "days": days,
        "last": now,
        "used": st.get("used", []) + [kid],
    }, None


def license_screen(dev, err=None):
    w = max(36, min(shutil.get_terminal_size((44, 24)).columns, 50))
    out = sys.stdout
    out.write("\033[2J\033[H")
    for line in logo_lines(w):
        out.write(line + "\n")
    for line in box("ACTIVATION", [
        dim("Device ID  ") + c(226, dev, True),
        "",
        dim("Send this Device ID to the owner"),
        dim("to get your license key."),
    ], w, 213):
        out.write(line + "\n")
    if err:
        out.write("\n" + c(196, "x " + err, True) + "\n")
    out.write("\n")
    out.flush()


def license_gate():
    dev = device_id()
    st = load_license(dev)
    err = None
    now = time.time()
    if st.get("end"):
        if now < st.get("last", 0) - 600:
            err = "Phone date/time was changed. Set the correct time."
        elif now > st["end"]:
            err = "License expired on " + fmt_dt(st["end"])
        else:
            lic = {"dev": dev, "end": st["end"], "st": st}
            touch_license(lic, now)
            return lic
    while True:
        license_screen(dev, err)
        try:
            key = input(c(51, "Enter key ", True) + dim("(blank = exit)") + c(51, " > ", True)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not key:
            return None
        new, err = activate(key, dev, st)
        if not err:
            save_license(dev, new)
            print(c(82, f"\nActivated! {new['days']} day(s), until {fmt_dt(new['end'])}", True))
            time.sleep(1.5)
            return {"dev": dev, "end": new["end"], "st": new}


# ───────────────────────── main ─────────────────────────
def main():
    lic = license_gate()
    if not lic:
        return

    try:
        subprocess.run(["termux-wake-lock"], timeout=5, capture_output=True)
    except Exception:
        pass

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((HOST, PORT))
    except OSError as e:
        print(f"Port {PORT} ဖွင့်မရပါ: {e}")
        print("တခြား proxy app ပိတ်ထားပါ၊ ဒါမှမဟုတ် PORT ကို ပြောင်းပါ။")
        return
    server.listen(100)
    threading.Thread(target=accept_loop, args=(server,), daemon=True).start()

    start = time.time()
    ips, ips_t = get_ips(), time.time()
    prev_s = prev_r = 0
    cur_s = cur_r = max_s = max_r = 0
    hist = deque(maxlen=120)
    last = time.time()

    expired = False
    lic_saved = time.time()
    out = sys.stdout
    out.write("\033[?1049h\033[?25l\033[2J")   # စာမျက်နာအသစ် + cursor ဖျောက်
    out.flush()
    try:
        while True:
            now = time.time()
            if now > lic["end"]:
                expired = True
                break
            if now - lic_saved > 60:
                touch_license(lic, now)
                lic_saved = now
            dt = max(now - last, 0.001)
            last = now
            with lock:
                s, r = stats["sent"], stats["recv"]
            cur_s = (s - prev_s) * 8 / dt
            cur_r = (r - prev_r) * 8 / dt
            prev_s, prev_r = s, r
            max_s, max_r = max(max_s, cur_s), max(max_r, cur_r)
            hist.append(cur_s + cur_r)

            if now - ips_t > 15:
                ips, ips_t = get_ips(), now

            cols = shutil.get_terminal_size((44, 24)).columns
            w = max(36, min(cols, 50))
            frame = render(w, ips, cur_s, cur_r, max_s, max_r, hist, start, lic)
            out.write("\033[H" + "".join(line + "\033[K\n" for line in frame) + "\033[J")
            out.flush()
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        out.write("\033[?25h\033[?1049l")
        out.flush()
        print("HTTP Proxy stopped")
        if expired:
            print("License expired. Contact the owner for a new key.")
        server.close()
        try:
            subprocess.run(["termux-wake-unlock"], timeout=5, capture_output=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
