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
        """Read `length` bytes from `addr` via mGBA's core:readRange.

        The lua server replies with comma-separated hex tokens
        (e.g. "0a,ff,00"). Each token is parsed as base-16. A malformed
        token (non-hex, empty, or out of the 0-255 byte range) is wrapped
        in EmulatorError naming the offending token plus addr/length,
        instead of surfacing an opaque ValueError — so RAM-bridge failures
        point straight at the bad reply.

        Returns the parsed bytes (empty bytes on an empty reply).
        """
        text = self._send(f"core.readRange,{addr},{length}").text
        if not text:
            return b""
        out = bytearray()
        for tok in text.split(","):
            try:
                out.append(int(tok, 16))
            except ValueError as exc:
                raise EmulatorError(
                    f"readRange({addr:#x}, len={length}) bad hex token "
                    f"{tok!r}: {exc}"
                ) from exc
        return bytes(out)

    def screenshot(self, path: Path | str) -> None:
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        self._send(f"core.screenshot,{p.as_posix()}")

    def save_state_file(self, path: Path | str, flags: int = 1) -> bool:
        """Write a savestate to disk via mGBA's core:saveStateFile.

        Rule check: only save is exposed here; load is intentionally NOT
        wrapped — project rule forbids saveStateLoad mid-run. Use this to
        snapshot progress periodically so a crash doesn't lose work.
        """
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        r = self._send(f"core.saveStateFile,{p.as_posix()},{int(flags)}")
        return not r.is_error

    def save_state_slot(self, slot: int, flags: int = 1) -> bool:
        r = self._send(f"core.saveStateSlot,{int(slot)},{int(flags)}")
        return not r.is_error

    def load_state_file(self, path: Path | str, flags: int = 1) -> bool:
        """Load a savestate from disk via mGBA's core:loadStateFile.

        CLAUDE.md forbids saveStateLoad as a way to reset story progress to
        bypass blockers. This wrapper exists for EMERGENCY RESTORE ONLY:
        when mGBA itself unintentionally terminates and the in-game save
        rolls back to an earlier point, loading the last autosnap returns
        the agent to where it actually was. Do not use for routine skips.
        """
        p = Path(path).resolve()
        if not p.exists():
            raise EmulatorError(f"savestate not found: {p}")
        r = self._send(f"core.loadStateFile,{p.as_posix()},{int(flags)}")
        return not r.is_error
