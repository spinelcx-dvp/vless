#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quick Tunnel + VLESS WS TLS generator
- Creates a Cloudflare Quick Tunnel
- Generates a random UUID
- Prints and saves the VLESS config (WS + TLS)
"""

import os
import re
import sys
import json
import uuid
import time
import signal
import shutil
import socket
import base64
import random
import string
import subprocess
import threading
from pathlib import Path

# ---------------- تنظیمات قابل تغییر ----------------
LOCAL_HOST = "127.0.0.1"          # آدرس محلی که کانفیگ روی آن اجرا می‌شود
LOCAL_PORT = 8080                 # پورت محلی VLESS
WS_PATH    = "/ws"                # مسیر WebSocket
CONFIG_OUT = "vless_config.txt"   # فایل خروجی
CLOUDFLARED = shutil.which("cloudflared") or "cloudflared"
# ----------------------------------------------------

TUNNEL_URL_RE = re.compile(r"https://([a-z0-9-]+\.trycloudflare\.com)")


def gen_uuid() -> str:
    """تولید UUID نسخه ۴"""
    return str(uuid.uuid4())


def rand_short_id(length: int = 8) -> str:
    """رشته تصادفی برای path یا نام‌های دلخواه"""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    """صبر می‌کند تا پورت محلی باز شود"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


class Tunnel:
    """مدیریت پروسه cloudflared"""

    def __init__(self, local_url: str):
        self.local_url = local_url
        self.proc: subprocess.Popen | None = None
        self.url: str | None = None
        self._lock = threading.Lock()

    def start(self, timeout: float = 40.0) -> str:
        cmd = [
            CLOUDFLARED,
            "tunnel",
            "--no-autoupdate",
            "--url", self.local_url,
        ]
        print(f"[*] اجرای: {' '.join(cmd)}")
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        start_time = time.time()
        for line in self.proc.stdout:  # type: ignore[union-attr]
            print(f"    [cloudflared] {line.rstrip()}")
            m = TUNNEL_URL_RE.search(line)
            if m:
                with self._lock:
                    self.url = f"https://{m.group(1)}"
                print(f"[+] تونل ساخته شد: {self.url}")
                return self.url
            if time.time() - start_time > timeout:
                break

        self.stop()
        raise RuntimeError("دریافت آدرس تونل از trycloudflare ناموفق بود.")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
        self.proc = None


def build_vless_config(uuid_str: str, sni: str, path: str, name: str = "CF-Quick") -> str:
    """ساخت لینک VLESS با فرمت استاندارد"""
    params = {
        "encryption": "none",
        "security": "tls",
        "sni": sni,
        "fp": "chrome",
        "type": "ws",
        "host": sni,
        "path": path,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"vless://{uuid_str}@{sni}:443?{query}#{name}"


def main():
    # ۱) UUID خودکار
    uid = gen_uuid()
    print(f"[+] UUID ساخته شد: {uid}")

    # ۲) بررسی وجود cloudflared
    if shutil.which(CLOUDFLARED) is None:
        print("[!] cloudflared پیدا نشد. لطفاً نصبش کن.", file=sys.stderr)
        sys.exit(1)

    # ۳) هشدار: باید یک سرور VLESS محلی روی پورت تعیین‌شده در حال اجرا باشد
    if not wait_for_port(LOCAL_HOST, LOCAL_PORT, timeout=3):
        print(
            f"[!] هشدار: هیچ سرویسی روی {LOCAL_HOST}:{LOCAL_PORT} گوش نمی‌دهد.\n"
            f"    قبل از اجرا، سرور VLESS/WS محلی خودت را بالا بیاور.",
            file=sys.stderr,
        )

    # ۴) ساخت تونل
    local_url = f"http://{LOCAL_HOST}:{LOCAL_PORT}"
    tunnel = Tunnel(local_url)

    try:
        public_url = tunnel.start()
    except Exception as e:
        print(f"[!] خطا در ساخت تونل: {e}", file=sys.stderr)
        sys.exit(1)

    sni = public_url.replace("https://", "").rstrip("/")

    # ۵) کانفیگ
    config = build_vless_config(uid, sni, WS_PATH, name="CF-Quick")

    print("\n" + "=" * 60)
    print("کانفیگ VLESS شما:")
    print("=" * 60)
    print(config)
    print("=" * 60)
    print(f"UUID     : {uid}")
    print(f"Address  : {sni}")
    print(f"Port     : 443")
    print(f"SNI      : {sni}")
    print(f"Host     : {sni}")
    print(f"Path     : {WS_PATH}")
    print(f"TLS      : true")
    print("=" * 60 + "\n")

    # ۶) ذخیره در فایل
    out = Path(CONFIG_OUT)
    out.write_text(
        json.dumps(
            {
                "uuid": uid,
                "address": sni,
                "port": 443,
                "sni": sni,
                "host": sni,
                "path": WS_PATH,
                "tls": True,
                "link": config,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"[+] کانفیگ در فایل ذخیره شد: {out.resolve()}")

    # ۷) منتظر ماندن تا CTRL+C
    print("[*] برای بستن تونل، CTRL+C را بزن...\n")

    def _shutdown(signum, frame):
        print("\n[*] در حال بستن تونل...")
        tunnel.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
