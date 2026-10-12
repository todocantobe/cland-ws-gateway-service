#!/usr/bin/env python3
"""cland-ws-gateway 一键接入工具（CLI + 库双用，零依赖仅需 websockets>=11）。
# 单一来源: ~/.agents/skills/cland-ws-gateway/scripts/gw.py（仓库副本，改动以全局为准）

用法（作为 agent 连接网关最简姿势）：
  python3 gw.py probe --cid agent_a                 # 连通性 + ping 往返
  python3 gw.py join  --cid agent_a --room lab      # 加入房间（幂等）
  python3 gw.py send  --cid agent_a --to agent_b --data '{"cmd":"hi"}'   # 定向
  python3 gw.py broadcast --cid agent_a --room lab --data hello          # 房间广播
  python3 gw.py listen --cid agent_a --join lab --exec ./handler.sh      # 长驻监听 + 回调
  echo -n $'\xaa\x01' | python3 gw.py send --cid agent_a --to agent_b --stdin  # 二进制透传

库模式：
  from gw import WSGateway
  gw = WSGateway("ws://127.0.0.1:8081/ws?cid=agent_a")
  gw.join("lab"); gw.send_to("agent_b", b'{"cmd":"hi"}'); gw.broadcast("lab", b"hi")
  for t, src, payload in gw.listen(): ...
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time

import websockets

DEFAULT_URL = os.environ.get("GW_URL", "ws://127.0.0.1:8081/ws")
HEARTBEAT_S = 20          # 应用级心跳间隔
MAX_NAME = 255            # dst/room 长度上限（u8）
EXIT_ERR = 3              # 收到错误帧的退出码


# ---------- 帧编解码（协议核心，小端，全部 WebSocket BinaryMessage） ----------

def frame_direct(dst: bytes, payload: bytes) -> bytes:
    return bytes([0x01, len(dst)]) + dst + payload

def frame_room(room: bytes, payload: bytes) -> bytes:
    return bytes([0x02, len(room)]) + room + payload

def frame_join(room: bytes) -> bytes:
    return bytes([0x03, len(room)]) + room

def frame_leave(room: bytes) -> bytes:
    return bytes([0x04, len(room)]) + room

def frame_ping() -> bytes:
    return bytes([0x05])

def parse_frame(f: bytes):
    """解析网关帧。返回 (kind, ...)：
    ('direct'|'room', src:str, payload:bytes) | ('join_ack'|'leave_ack', room:str)
    | ('pong',) | ('error', code:int, msg:str) | ('unknown', t:int)"""
    if not f:
        return ("unknown", -1)
    t = f[0]
    if t in (0x01, 0x02):
        sl = f[1]; src = f[2:2 + sl].decode("utf-8", "replace")
        return ("direct" if t == 0x01 else "room", src, f[2 + sl:])
    if t in (0x03, 0x04):
        rl = f[1]; return (("join_ack" if t == 0x03 else "leave_ack"), f[2:2 + rl].decode("utf-8", "replace"))
    if t == 0x05:
        return ("pong",)
    if t == 0xFF:
        return ("error", f[1], f[3:3 + f[2]].decode("utf-8", "replace"))
    return ("unknown", t)

ERROR_CODES = {0x01: "帧头非法", 0x02: "定向目标离线", 0x03: "未知帧类型", 0x04: "参数错误"}


# ---------- 库封装 ----------

class WSGateway:
    """网关客户端封装。用法见文件头 docstring。"""

    def __init__(self, url: str, cid: str, heartbeat: float = HEARTBEAT_S):
        self.url = url if "cid=" in url else f"{url}?cid={cid}"
        self.cid = cid
        self.heartbeat = heartbeat
        self.ws = None
        self._hb_task = None

    async def connect(self):
        self.ws = await websockets.connect(self.url, max_size=None)
        self._hb_task = asyncio.create_task(self._heartbeat_loop())
        return self

    async def close(self):
        if self._hb_task:
            self._hb_task.cancel()
        if self.ws:
            await self.ws.close()

    async def _heartbeat_loop(self):
        """应用级心跳：定期发 [05]。服务端另有协议级 ping（25s/次），双保险。"""
        while True:
            await asyncio.sleep(self.heartbeat)
            try:
                await self.ws.send(frame_ping())
            except Exception:
                return

    async def join(self, room: str) -> None:
        r = room.encode()
        assert len(r) <= MAX_NAME, f"room 长度超限: {len(r)} > {MAX_NAME}"
        await self.ws.send(frame_join(r))

    async def leave(self, room: str) -> None:
        r = room.encode()
        assert len(r) <= MAX_NAME, f"room 长度超限: {len(r)} > {MAX_NAME}"
        await self.ws.send(frame_leave(r))

    async def send_to(self, dst: str, payload: bytes) -> None:
        d = dst.encode()
        assert len(d) <= MAX_NAME, f"dst 长度超限: {len(d)} > {MAX_NAME}"
        await self.ws.send(frame_direct(d, payload))

    async def broadcast(self, room: str, payload: bytes) -> None:
        r = room.encode()
        assert len(r) <= MAX_NAME, f"room 长度超限: {len(r)} > {MAX_NAME}"
        await self.ws.send(frame_room(r, payload))

    async def ping_once(self) -> float:
        """发 [05] 等 pong，返回 RTT 秒；超时 3s 抛异常。"""
        t0 = time.monotonic()
        await self.ws.send(frame_ping())
        while True:
            f = await asyncio.wait_for(self.ws.recv(), timeout=3)
            if f and f[0] == 0x05:
                return time.monotonic() - t0

    async def listen(self, join_rooms=()):
        """帧迭代器。yield 解析结果；错误帧抛 GatewayError。"""
        for r in join_rooms:
            await self.join(r)
        async for raw in self.ws:
            parsed = parse_frame(raw)
            if parsed[0] == "error":
                _, code, msg = parsed
                raise GatewayError(code, msg)
            yield parsed

    async def __aenter__(self):
        return await self.connect()

    async def __aexit__(self, *exc):
        await self.close()


class GatewayError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(f"网关错误 0x{code:02X} {ERROR_CODES.get(code, '?')}: {msg}")
        self.code = code


# ---------- CLI ----------

def _load_payload(args) -> bytes:
    if args.stdin:
        return sys.stdin.buffer.read()
    if args.file:
        with open(args.file, "rb") as fh:
            return fh.read()
    if args.hex:
        return bytes.fromhex(args.hex)
    return args.data.encode("utf-8")


def _show(parsed):
    """帧展示。一律 flush=True：stdout 被重定向/管道时也必须即时可见，
    否则 timeout/被杀进程会丢输出。"""
    kind = parsed[0]
    if kind in ("direct", "room"):
        _, src, payload = parsed
        extra = ""
        try:
            extra = payload.decode("utf-8")
        except UnicodeDecodeError:
            extra = f"(hex {payload.hex()})"
        print(json.dumps({"type": kind, "src": src, "payload": extra, "payload_hex": payload.hex()}, ensure_ascii=False), flush=True)
    elif kind in ("join_ack", "leave_ack"):
        print(json.dumps({"type": kind, "room": parsed[1]}, ensure_ascii=False), flush=True)
    elif kind == "pong":
        pass
    else:
        print(json.dumps({"type": kind, "raw": parsed[1:]}, ensure_ascii=False), flush=True)


async def cmd_probe(args):
    async with WSGateway(args.url, args.cid) as gw:
        rtt = await gw.ping_once()
        print(f"OK cid={args.cid} rtt={rtt * 1000:.1f}ms", flush=True)


async def cmd_join_leave(args):
    async with WSGateway(args.url, args.cid) as gw:
        fn = gw.join if args.cmd == "join" else gw.leave
        for room in args.room:
            await fn(room)
        # 等待确认
        async for parsed in gw.listen():
            if parsed[0] == ("join_ack" if args.cmd == "join" else "leave_ack"):
                _show(parsed)
                break


async def cmd_send(args):
    payload = _load_payload(args)
    async with WSGateway(args.url, args.cid) as gw:
        await gw.send_to(args.to, payload)
        if args.wait:
            async for parsed in gw.listen():
                if parsed[0] == "error":
                    _show(parsed); sys.exit(EXIT_ERR)
                if parsed[0] == "direct" and parsed[1] == args.to:  # 等回音（对方转发回来）
                    _show(parsed); break


async def cmd_broadcast(args):
    payload = _load_payload(args)
    async with WSGateway(args.url, args.cid) as gw:
        await gw.broadcast(args.room, payload)
        await asyncio.sleep(0.1)


async def cmd_listen(args):
    """长驻监听。--exec <cmd>：每收到 direct/room 帧，执行 cmd 并把帧信息作为 JSON 喂给 stdin。"""
    async with WSGateway(args.url, args.cid) as gw:
        async for parsed in gw.listen(join_rooms=args.join or ()):
            if parsed[0] in ("direct", "room") and args.exec:
                _, src, payload = parsed
                info = {"type": parsed[0], "src": src,
                        "payload": payload.decode("utf-8", "replace"), "payload_hex": payload.hex(),
                        "from": args.cid, "ts": time.time()}
                proc = subprocess.run(args.exec, shell=True, input=json.dumps(info),
                                      capture_output=True, text=True, timeout=30)
                if proc.stdout.strip():
                    print(proc.stdout.strip(), flush=True)
                if proc.returncode != 0 and proc.stderr.strip():
                    print(f"[handler err {proc.returncode}] {proc.stderr.strip()}", file=sys.stderr, flush=True)
            else:
                _show(parsed)


def build_parser():
    p = argparse.ArgumentParser(prog="gw", description="cland-ws-gateway 接入工具（帧转发网关 8081）")
    p.add_argument("--url", default=DEFAULT_URL, help=f"网关地址（默认 {DEFAULT_URL}，可用 GW_URL 覆盖）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--cid", required=True, help="本端身份（网关内唯一，重复连接顶替旧连接）")

    sp = sub.add_parser("probe", help="连通性测试：连接 + [05] ping/pong 往返")
    common(sp)

    sp = sub.add_parser("join", help="加入房间（幂等）"); common(sp)
    sp.add_argument("--room", action="append", required=True, help="房间名（可多次）")
    sp = sub.add_parser("leave", help="离开房间（幂等）"); common(sp)
    sp.add_argument("--room", action="append", required=True, help="房间名（可多次）")

    sp = sub.add_parser("send", help="定向转发给单个用户")
    common(sp); sp.add_argument("--to", required=True, help="目标 cid")
    sp.add_argument("--data", default="", help="payload 文本（UTF-8）")
    sp.add_argument("--hex", help="payload 十六进制，如 aabb01")
    sp.add_argument("--file", help="payload 从文件读（二进制）")
    sp.add_argument("--stdin", action="store_true", help="payload 从 stdin 读（二进制）")
    sp.add_argument("--wait", action="store_true", help="发送后等待对方回音/错误帧")

    sp = sub.add_parser("broadcast", help="房间广播（除发送者外所有成员）")
    common(sp); sp.add_argument("--room", required=True)
    sp.add_argument("--data", default=""); sp.add_argument("--hex")
    sp.add_argument("--file"); sp.add_argument("--stdin", action="store_true")

    sp = sub.add_parser("listen", help="长驻监听：收到帧打印 JSON 行；--exec 可挂外部处理命令")
    common(sp)
    sp.add_argument("--join", action="append", help="启动时加入的房间（可多次）")
    sp.add_argument("--exec", help="收到投递帧时执行的 shell 命令（帧信息 JSON 走 stdin）")
    return p


def main():
    args = build_parser().parse_args()
    cmds = {"probe": cmd_probe, "join": cmd_join_leave, "leave": cmd_join_leave,
            "send": cmd_send, "broadcast": cmd_broadcast, "listen": cmd_listen}
    try:
        asyncio.run(cmds[args.cmd](args))
    except GatewayError as e:
        print(str(e), file=sys.stderr); sys.exit(EXIT_ERR)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"失败: {e}", file=sys.stderr); sys.exit(1)


if __name__ == "__main__":
    main()
