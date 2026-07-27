#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量测活代理：能否经代理访问 accounts.x.ai（注册相关 HTTPS）。

用法:
  # 1) 把网上拿到的代理写入 proxies_raw.txt（每行一个）
  # 2) 测活
  python check_proxies.py proxies_raw.txt

  # 只输出最快的前 10 个
  python check_proxies.py proxies_raw.txt -n 10

  # 自定义超时 / 并发
  python check_proxies.py proxies_raw.txt -t 12 -j 40

  # 测完后把最快的一个写进 .env 的 GROK_PROXY（会改本地 .env）
  python check_proxies.py proxies_raw.txt --apply-best

输入格式（与项目 GROK_PROXY 一致）:
  host:port
  http://host:port
  socks5://host:port
  user:pass@host:port
  host:port:user:pass
  http://user:pass@host:port

输出:
  proxies_ok.txt          — 可用代理（按延迟升序，可直接当 GROK_PROXY）
  proxies_ok_detail.csv   — 含状态码 / 延迟 / 错误摘要
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("需要 requests: pip install requests", file=sys.stderr)
    sys.exit(1)

# 注册主站；能 CONNECT + TLS 到这里才有意义
DEFAULT_PROBE_URL = "https://accounts.x.ai/"
# 备用：部分代理会拦 x.ai 但能出网；仅作诊断
FALLBACK_PROBE_URL = "https://httpbin.org/ip"

_BASE = Path(__file__).resolve().parent


def parse_proxy_spec(spec: str) -> Optional[dict[str, str]]:
    """与 g.same_session_register.parse_proxy_spec 对齐（独立脚本，避免重依赖）。"""
    s = (spec or "").strip()
    if not s or s.startswith("#"):
        return None
    if "://" in s:
        m = re.match(
            r"^(?P<scheme>https?|socks5h?|socks4)://"
            r"(?:(?P<user>[^:@/]+):(?P<pass>[^@/]+)@)?"
            r"(?P<host>[^:/]+):(?P<port>\d+)/?$",
            s,
            re.I,
        )
        if not m:
            return {"server": s, "server_url": s, "raw": s}
        scheme = m.group("scheme")
        host = m.group("host")
        port = m.group("port")
        user = m.group("user")
        pw = m.group("pass")
        server = f"{scheme}://{host}:{port}"
        out: dict[str, str] = {"server": server, "raw": s}
        if user:
            out["username"] = user
            out["password"] = pw or ""
            out["server_url"] = (
                f"{scheme}://{quote(user, safe='')}:{quote(pw or '', safe='')}@"
                f"{host}:{port}"
            )
        else:
            out["server_url"] = server
        return out

    if "@" in s:
        cred, hostport = s.rsplit("@", 1)
        if ":" in cred and ":" in hostport:
            user, pw = cred.split(":", 1)
            host, port = hostport.rsplit(":", 1)
            server = f"http://{host}:{port}"
            return {
                "server": server,
                "username": user,
                "password": pw,
                "server_url": (
                    f"http://{quote(user, safe='')}:{quote(pw, safe='')}@{host}:{port}"
                ),
                "raw": s,
            }

    parts = s.split(":")
    if len(parts) >= 4 and parts[1].isdigit():
        host, port = parts[0], parts[1]
        user = ":".join(parts[2:-1])
        pw = parts[-1]
        server = f"http://{host}:{port}"
        return {
            "server": server,
            "username": user,
            "password": pw,
            "server_url": (
                f"http://{quote(user, safe='')}:{quote(pw, safe='')}@{host}:{port}"
            ),
            "raw": s,
        }

    if len(parts) == 2 and parts[1].isdigit():
        server = f"http://{parts[0]}:{parts[1]}"
        return {"server": server, "server_url": server, "raw": s}
    return None


def load_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # 兼容 "ip port" / "ip\tport"
        if " " in s or "\t" in s:
            bits = re.split(r"[\s,;]+", s)
            if len(bits) >= 2 and bits[1].isdigit():
                s = f"{bits[0]}:{bits[1]}"
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def grok_proxy_value(parsed: dict[str, str]) -> str:
    """输出可直接写入 GROK_PROXY 的字符串（优先简洁 raw）。"""
    raw = (parsed.get("raw") or "").strip()
    if raw:
        return raw
    return parsed.get("server_url") or parsed.get("server") or ""


def probe_one(
    raw: str,
    *,
    url: str,
    timeout: float,
) -> dict[str, Any]:
    parsed = parse_proxy_spec(raw)
    if not parsed:
        return {
            "raw": raw,
            "ok": False,
            "ms": None,
            "status": None,
            "error": "invalid_format",
            "grok_proxy": "",
        }

    server_url = parsed.get("server_url") or parsed.get("server") or ""
    proxies = {"http": server_url, "https": server_url}
    t0 = time.perf_counter()
    try:
        # HEAD 有的代理/站点不支持；用 GET 只读头更稳
        r = requests.get(
            url,
            proxies=proxies,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,*/*",
            },
        )
        ms = int((time.perf_counter() - t0) * 1000)
        # 2xx/3xx/403/401 都说明 TCP+TLS+HTTP 通了（CF 拦业务也算链路通）
        ok = r.status_code < 500
        return {
            "raw": raw,
            "ok": ok,
            "ms": ms,
            "status": r.status_code,
            "error": "" if ok else f"http_{r.status_code}",
            "grok_proxy": grok_proxy_value(parsed),
        }
    except requests.exceptions.ProxyError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        return {
            "raw": raw,
            "ok": False,
            "ms": ms,
            "status": None,
            "error": f"proxy_error:{_short(e)}",
            "grok_proxy": grok_proxy_value(parsed),
        }
    except requests.exceptions.ConnectTimeout:
        ms = int((time.perf_counter() - t0) * 1000)
        return {
            "raw": raw,
            "ok": False,
            "ms": ms,
            "status": None,
            "error": "connect_timeout",
            "grok_proxy": grok_proxy_value(parsed),
        }
    except requests.exceptions.ReadTimeout:
        ms = int((time.perf_counter() - t0) * 1000)
        return {
            "raw": raw,
            "ok": False,
            "ms": ms,
            "status": None,
            "error": "read_timeout",
            "grok_proxy": grok_proxy_value(parsed),
        }
    except requests.exceptions.SSLError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        return {
            "raw": raw,
            "ok": False,
            "ms": ms,
            "status": None,
            "error": f"ssl:{_short(e)}",
            "grok_proxy": grok_proxy_value(parsed),
        }
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        return {
            "raw": raw,
            "ok": False,
            "ms": ms,
            "status": None,
            "error": f"{type(e).__name__}:{_short(e)}",
            "grok_proxy": grok_proxy_value(parsed),
        }


def _short(e: BaseException, n: int = 80) -> str:
    s = str(e).replace("\n", " ").strip()
    return s[:n] + ("…" if len(s) > n else "")


def apply_best_to_env(proxy: str, env_path: Path) -> None:
    line = f"GROK_PROXY={proxy}"
    if not env_path.exists():
        example = _BASE / ".env.example"
        if example.exists():
            text = example.read_text(encoding="utf-8")
        else:
            text = ""
        if re.search(r"^GROK_PROXY=", text, re.M):
            text = re.sub(r"^GROK_PROXY=.*$", line, text, count=1, flags=re.M)
        else:
            text = (text.rstrip() + "\n\n" + line + "\n") if text else line + "\n"
        env_path.write_text(text, encoding="utf-8")
        print(f"[*] 已创建 {env_path} 并写入 {line}")
        return

    text = env_path.read_text(encoding="utf-8")
    if re.search(r"^GROK_PROXY=", text, re.M):
        text = re.sub(r"^GROK_PROXY=.*$", line, text, count=1, flags=re.M)
    else:
        text = text.rstrip() + "\n" + line + "\n"
    env_path.write_text(text, encoding="utf-8")
    print(f"[*] 已更新 {env_path}: {line}")


def main() -> int:
    ap = argparse.ArgumentParser(description="测活代理 → 输出 GROK_PROXY 候选")
    ap.add_argument(
        "input",
        nargs="?",
        default="proxies_raw.txt",
        help="代理列表文件（默认 proxies_raw.txt）",
    )
    ap.add_argument(
        "-o",
        "--output",
        default="proxies_ok.txt",
        help="可用代理输出（默认 proxies_ok.txt）",
    )
    ap.add_argument(
        "--csv",
        default="proxies_ok_detail.csv",
        help="明细 CSV（默认 proxies_ok_detail.csv）",
    )
    ap.add_argument(
        "-u",
        "--url",
        default=DEFAULT_PROBE_URL,
        help=f"探测 URL（默认 {DEFAULT_PROBE_URL}）",
    )
    ap.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=12.0,
        help="单代理超时秒数（默认 12）",
    )
    ap.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=32,
        help="并发数（默认 32）",
    )
    ap.add_argument(
        "-n",
        "--top",
        type=int,
        default=0,
        help="只保留延迟最低的前 N 个（0=全部可用）",
    )
    ap.add_argument(
        "--fallback",
        action="store_true",
        help=f"主站失败时再用 {FALLBACK_PROBE_URL} 测「能否出网」（仅诊断）",
    )
    ap.add_argument(
        "--apply-best",
        action="store_true",
        help="把延迟最低的可用代理写入 .env 的 GROK_PROXY",
    )
    ap.add_argument(
        "--env",
        default=".env",
        help="--apply-best 时写入的 env 路径（默认 .env）",
    )
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"[!] 找不到输入文件: {in_path}", file=sys.stderr)
        print(
            "    请先创建，例如:\n"
            "      echo '1.2.3.4:8080' > proxies_raw.txt\n"
            "      echo 'socks5://5.6.7.8:1080' >> proxies_raw.txt",
            file=sys.stderr,
        )
        return 1

    lines = load_lines(in_path)
    if not lines:
        print(f"[!] {in_path} 里没有有效代理行", file=sys.stderr)
        return 1

    print(f"[*] 读取 {len(lines)} 个代理 · 探测 {args.url} · 超时 {args.timeout}s · 并发 {args.jobs}")
    results: list[dict[str, Any]] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {
            ex.submit(probe_one, raw, url=args.url, timeout=args.timeout): raw
            for raw in lines
        }
        for fut in as_completed(futs):
            row = fut.result()
            results.append(row)
            done += 1
            mark = "OK" if row["ok"] else "FAIL"
            ms = row["ms"] if row["ms"] is not None else "-"
            st = row["status"] if row["status"] is not None else "-"
            err = row["error"] or ""
            print(
                f"  [{done}/{len(lines)}] {mark:4} {ms:>5}ms  http={st}  {row['raw'][:48]}"
                + (f"  ({err})" if err and not row["ok"] else "")
            )

    ok_rows = [r for r in results if r["ok"]]
    ok_rows.sort(key=lambda r: (r["ms"] is None, r["ms"] or 10**9))

    if args.top and args.top > 0:
        ok_rows = ok_rows[: args.top]

    out_path = Path(args.output)
    out_path.write_text(
        "\n".join(r["grok_proxy"] or r["raw"] for r in ok_rows) + ("\n" if ok_rows else ""),
        encoding="utf-8",
    )

    csv_path = Path(args.csv)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["ok", "ms", "status", "grok_proxy", "raw", "error"],
        )
        w.writeheader()
        for r in sorted(
            results,
            key=lambda x: (not x["ok"], x["ms"] is None, x["ms"] or 10**9),
        ):
            w.writerow(
                {
                    "ok": int(bool(r["ok"])),
                    "ms": r["ms"] if r["ms"] is not None else "",
                    "status": r["status"] if r["status"] is not None else "",
                    "grok_proxy": r.get("grok_proxy") or "",
                    "raw": r.get("raw") or "",
                    "error": r.get("error") or "",
                }
            )

    print()
    print(f"[*] 合计 {len(results)} · 可用 {len(ok_rows)} · 不可用 {len(results) - len([r for r in results if r['ok']])}")
    print(f"[*] 可用列表: {out_path.resolve()}")
    print(f"[*] 明细 CSV: {csv_path.resolve()}")

    if ok_rows:
        best = ok_rows[0]
        best_val = best["grok_proxy"] or best["raw"]
        print(f"[*] 最快: {best_val}  ({best['ms']}ms, HTTP {best['status']})")
        print()
        print("写入 .env 示例:")
        print(f"  GROK_PROXY={best_val}")
        print("或控制台「配置 → 注册代理」粘贴同一串。")
        if args.apply_best:
            apply_best_to_env(best_val, Path(args.env))
    else:
        print("[!] 没有可用代理。")
        print("    常见原因: 代理已死 / 不支持 HTTPS CONNECT / 被墙 / 超时过短")
        if args.fallback:
            print(f"[*] 改用备用 URL 再测出网: {FALLBACK_PROBE_URL}")
            fb_ok = 0
            for raw in lines[: min(50, len(lines))]:
                row = probe_one(raw, url=FALLBACK_PROBE_URL, timeout=args.timeout)
                if row["ok"]:
                    fb_ok += 1
                    print(f"  出网 OK  {row['ms']}ms  {raw}")
            print(f"[*] 备用探测可用约 {fb_ok} 个（能出网 ≠ 能注册 xAI）")
        else:
            print("    可加 --fallback 看是否「能出网但访问不了 xAI」")

    return 0 if ok_rows else 2


if __name__ == "__main__":
    sys.exit(main())
