"""
mGBA socket client.

mgba_scripts/mGBASocketServer_generic.lua (port 8895) と話す。
既存 training の pokemon_env.py には依存しない (独立実装)。
"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from pathlib import Path

from . import config


class EmulatorError(RuntimeError):
    pass


@dataclass
class RawResponse:
    text: str

    @property
    def is_error(self) -> bool:
        return self.text == config.SOCKET_ERROR

    @property
    def is_success(self) -> bool:
        return self.text == config.SOCKET_SUCCESS


class MGBAClient:
    """1 socket connection で 1 命令ずつ送信。

    lua 側は <|END|> 区切り。 接続使い回しは不安定なので
    1 命令ごとに connect → send → recv → close する。
    """

    def __init__(
        self,
        host: str = config.SOCKET_HOST,
        port: int = config.SOCKET_PORT,
        timeout: float = config.SOCKET_TIMEOUT_SEC,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def _send(self, payload: str) -> RawResponse:
        last_err: Exception | None = None
        for attempt in range(config.SOCKET_CONNECT_RETRIES):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            try:
                sock.connect((self.host, self.port))
                sock.sendall(
                    (payload + config.SOCKET_TERMINATOR).encode("utf-8")
                )
                buf = bytearray()
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if config.SOCKET_TERMINATOR.encode() in bytes(buf):
                        break
                text = bytes(buf).decode("utf-8", errors="replace")
                text = text.replace(config.SOCKET_TERMINATOR, "").strip()
                return RawResponse(text=text)
            except (TimeoutError, ConnectionError, OSError) as exc:
                last_err = exc
                time.sleep(0.2 * (attempt + 1))
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
        raise EmulatorError(
            f"socket send failed after {config.SOCKET_CONNECT_RETRIES} retries: "
            f"{last_err!r}"
        )

    def ping(self) -> bool:
        try:
            r = self._send(config.SOCKET_ACK)
        except EmulatorError:
            return False
        return not r.is_error

    def get_game_title(self) -> str:
        return self._send("core.getGameTitle,").text

    def get_game_code(self) -> str:
        return self._send("core.getGameCode,").text

    def current_frame(self) -> int:
        return int(self._send("core.currentFrame,").text)

    def tap(self, button: str, frames: int = 15) -> None:
        if button not in {
            "A", "B", "Start", "Select",
            "Up", "Down", "Left", "Right",
            "L", "R",
        }:
            raise ValueError(f"invalid button: {button}")
        self._send(f"mgba-http.button.hold,{button},{int(frames)}")

    def _parse_int(self, raw: str, op: str, addr: int) -> int:
        t = (raw or "").strip()
        if not t or not t.lstrip("-").isdigit():
            raise EmulatorError(
                f"{op}({addr:#x}) non-numeric reply: {raw!r}"
            )
        return int(t)

    def read8(self, addr: int) -> int:
        return self._parse_int(
            self._send(f"core.read8,{addr}").text, "read8", addr
        )

    def read16(self, addr: int) -> int:
        return self._parse_int(
            self._send(f"core.read16,{addr}").text, "read16", addr
        )

    def read32(self, addr: int) -> int:
        return self._parse_int(
            self._send(f"core.read32,{addr}").text, "read32", addr
        )

    def read_range(self, addr: int, length: int) -> bytes:
        text = self._send(f"core.readRange,{addr},{length}").text
        if not text:
            return b""
        return bytes(int(b, 16) for b in text.split(","))

    def screenshot(self, path: Path | str) -> None:
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        self._send(f"core.screenshot,{p.as_posix()}")
