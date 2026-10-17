'''
Control sensor board stage over wifi.

py ESP_http_handle.py status
py ESP_http_handle.py stage 1          reboot esp board to initial stage, with id check and live read out
py ESP_http_handle.py stage 2          reboot esp board to stage 2, with odr check and self test
py ESP_http_handle.py stage 3          reboot esp board to stage 3, start a new log in sd card
py ESP_http_handle.py stop             stop stage-3 logging and close the file
py ESP_http_handle.py log              show the board's console output (Ctrl-C to quit)
py ESP_http_handle.py ota .pio/build/<env>/firmware.bin

'''
import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

class BoardError(Exception):
    pass

def request(host, token, method, path, data=None, timeout=10.0):
    req = urllib.request.Request(f"http://{host}{path}", data=data, method=method)
    req.add_header("X-ota-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise BoardError(f"HTTP {e.code}: {e.read().decode(errors='replace')}") from None
    except (urllib.error.URLError, OSError) as e:
        raise BoardError(f"can't reach {host}: {getattr(e, 'reason', e)}") from None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body.decode(errors="replace")}
    
def call(*args, **kw):
    try:
        return request(*args, **kw)
    except BoardError as e:
        sys.exit(str(e))

def show(obj):
    print(json.dumps(obj, indent=2))


def wait_for_board(host, token, deadline_s=60.0):
    t_end = time.monotonic() + deadline_s
    time.sleep(3)
    while time.monotonic() < t_end:
        try:
            return request(host, token, "GET", "/status", timeout=3)
        except BoardError:
            time.sleep(2)
    return None

def cmd_stage(host, token, n):
    show(call(host, token, "POST", f"/stage?n={n}", data=b""))
    print("waiting for reboot ...")
    after = wait_for_board(host, token)
    if after is None:
        sys.exit("board didn't come back within 60 s")
    show(after)
    if after.get("stage") != n:
        print(f"WARNING: board is running stage {after.get('stage')}, not {n}")

def cmd_ota(host, token, path):
    with open(path, "rb") as f:
        image = f.read()
    before = call(host, token, "GET", "/status")
    if before.get("logging"):
        sys.exit("board is logging; run 'bob.py stop' first")
    print(f"uploading {len(image):,} bytes "
          f"(currently {before.get('fw')} on {before.get('partition')}) ...")
    show(call(host, token, "POST", "/ota", data=image, timeout=180.0))

    print("waiting for reboot ...")
    after = wait_for_board(host, token)
    if after is None:
        sys.exit("board didn't come back within 60 s. A new image that fails its "
                 "boot check rolls back on its own; try 'status' in a minute.")
    show(after)
    if after.get("partition") == before.get("partition"):
        print("WARNING: still on the old partition, so the new image was rolled back.")
    else:
        print(f"OK: now running {after.get('fw')} from {after.get('partition')}")

def cmd_log(host, token, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", port))
    sock.settimeout(1.0)
    print(f"listening on UDP {port}; Ctrl-C to quit", file=sys.stderr)
    last_sub, subscribed = 0.0, False
    try:
        while True:
            # After board reboot need to reconnect.
            if time.monotonic() - last_sub > 10:
                last_sub = time.monotonic()
                try:
                    request(host, token, "POST", f"/log?port={port}", data=b"", timeout=3)
                    if not subscribed:
                        print("[subscribed]", file=sys.stderr)
                    subscribed = True
                except BoardError as e:
                    if subscribed:
                        print(f"[lost board: {e}; retrying]", file=sys.stderr)
                    subscribed = False
            try:
                data, _ = sock.recvfrom(2048)
                sys.stdout.write(data.decode(errors="replace"))
                sys.stdout.flush()
            except socket.timeout:
                pass
    except KeyboardInterrupt:
        pass

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=os.environ.get("ESP_HOST", "esp.local"))
    #change the default ip in the system environment var
    p.add_argument("--token", default=os.environ.get("OTA_TOKEN", "its a secret dude"))
    #remember to add the randomly generated ota token to environment variables
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("stage")
    s.add_argument("n", type=int, choices=[1, 2, 3])
    sub.add_parser("stop")
    lg = sub.add_parser("log")
    lg.add_argument("--port", type=int, default=5555)
    o = sub.add_parser("ota")
    o.add_argument("firmware", help="path to firmware.bin")
    a = p.parse_args()

    if a.cmd == "status":
        show(call(a.host, a.token, "GET", "/status"))
    elif a.cmd == "stage":
        cmd_stage(a.host, a.token, a.n)
    elif a.cmd == "stop":
        show(call(a.host, a.token, "POST", "/run/stop", data=b""))
    elif a.cmd == "log":
        cmd_log(a.host, a.token, a.port)
    elif a.cmd == "ota":
        cmd_ota(a.host, a.token, a.firmware)


if __name__ == "__main__":
    main()
