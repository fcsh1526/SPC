"""The OPC UA client (asyncua, optional dependency `opcua`): a test read of the nodes, and the runner that keeps the enabled links subscribed."""

from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timezone
from typing import Any

from spc.equipment.model import EquipmentError

CONNECT_TIMEOUT = 10
BACKOFF = (2, 5, 10, 30, 60)


def _asyncua():
    try:
        import asyncua  # noqa: F401
        from asyncua import Client, ua
    except ImportError:
        raise EquipmentError("opcua_not_installed", "the OPC UA client is not installed: pip install 'spc[opcua]'", 501) from None
    return Client, ua


def _client(link: dict):
    Client, _ = _asyncua()
    c = Client(link["endpoint"], timeout=CONNECT_TIMEOUT)
    if link["username"]:
        pw = os.environ.get(link["password_env"])
        if pw is None:
            raise EquipmentError("opcua_password_missing", f"the environment variable {link['password_env']} is not set", 409, name=link["password_env"])
        c.set_user(link["username"])
        c.set_password(pw)
    return c, link["security"]


async def _connect(link: dict):
    c, security = _client(link)
    if security != "none":
        await c.set_security_string(security)
    await c.connect()
    return c


def _status(dv):
    for name in ("StatusCode", "StatusCode_"):  # the name differs between asyncua versions
        code = getattr(dv, name, None)
        if code is not None:
            return code
    return None


def _good(dv) -> bool:
    code = _status(dv)
    return True if code is None else bool(code.is_good())


async def _probe(link: dict) -> dict:
    out: dict[str, Any] = {"ok": False, "error": None, "nodes": []}
    try:
        c = await _connect(link)
    except EquipmentError:
        raise
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return out
    try:
        for n in link["nodes"]:
            item = {"node_id": n["node_id"], "ok": False, "value": None, "type": None, "quality": None, "source_time": None, "error": None}
            try:
                dv = await c.get_node(n["node_id"]).read_data_value()
                v = dv.Value.Value if dv.Value is not None else None
                item["type"] = dv.Value.VariantType.name if dv.Value is not None else None
                item["quality"] = "good" if _good(dv) else str(_status(dv))
                item["source_time"] = dv.SourceTimestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dv.SourceTimestamp else None
                numeric = isinstance(v, (int, float)) and not isinstance(v, bool)
                item["value"] = v if numeric else (None if v is None else str(v)[:60])
                item["ok"] = bool(_good(dv) and numeric and item["source_time"])
                if not item["ok"]:
                    item["error"] = "bad_quality" if not _good(dv) else "not_numeric" if not numeric else "no_timestamp"
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"[:200]
            out["nodes"].append(item)
        out["ok"] = all(i["ok"] for i in out["nodes"])
    finally:
        try:
            await c.disconnect()
        except Exception:
            pass
    return out


def probe(link: dict) -> dict:
    """Connect once and read every node: value, type, quality and source timestamp. Runs in its own event loop, so a route can call it."""
    _asyncua()
    return asyncio.run(_probe(link))


class Runner:
    """One thread with an event loop. For every enabled link it keeps a connection and a subscription, and reconnects with a growing pause."""

    def __init__(self, store, ingest):
        self.store, self.ingest = store, ingest
        self.status: dict[int, dict] = {}
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        _asyncua()
        if self.running:
            return
        self._stop.clear()
        ready = threading.Event()
        self._thread = threading.Thread(target=self._main, args=(ready,), name="spc-opcua", daemon=True)
        self._thread.start()
        ready.wait(5)

    def stop(self) -> None:
        self._stop.set()
        self.reconcile()
        if self._thread:
            self._thread.join(10)

    def reconcile(self) -> None:
        loop, wake = self._loop, self._wake
        if loop and wake and loop.is_running():
            loop.call_soon_threadsafe(wake.set)

    def _main(self, ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop, self._wake = loop, asyncio.Event()
        ready.set()
        try:
            loop.run_until_complete(self._supervise())
        finally:
            loop.close()

    def _desired(self) -> dict[int, dict]:
        return {l["id"]: l for l in self.store.all() if l.get("enabled")}

    async def _supervise(self) -> None:
        tasks: dict[int, tuple[int, asyncio.Task]] = {}  # link id -> (revision, task)
        while not self._stop.is_set():
            desired = self._desired()
            for lid, (rev, task) in list(tasks.items()):
                if lid not in desired or desired[lid]["revision"] != rev or task.done():
                    task.cancel()
                    try:
                        await task
                    except BaseException:
                        pass
                    del tasks[lid]
                    if lid not in desired:
                        self.status.pop(lid, None)
            for lid, link in desired.items():
                if lid not in tasks:
                    tasks[lid] = (link["revision"], asyncio.create_task(self._run_link(link)))
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), 5)
            except asyncio.TimeoutError:
                pass
        for _, task in tasks.values():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        self.status.clear()

    def _set(self, lid: int, state: str, message: str = "") -> None:
        self.status[lid] = {"state": state, "message": message, "since": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}

    async def _run_link(self, link: dict) -> None:
        lid = link["id"]
        _, ua = _asyncua()
        attempt = 0
        ingest = self.ingest
        by_id = {ua.NodeId.from_string(n["node_id"]).to_string(): n["node_id"] for n in link["nodes"]}

        class Handler:
            def datachange_notification(self, node, val, data):
                dv = data.monitored_item.Value
                key = by_id.get(node.nodeid.to_string())
                if key is None:
                    return
                try:
                    ingest.accept(lid, key, val, _good(dv), dv.SourceTimestamp)
                except Exception as exc:  # one bad reading must not end the subscription
                    self_status[0] = f"{type(exc).__name__}: {exc}"[:200]

        self_status = [""]
        while True:
            client = None
            try:
                self._set(lid, "connecting")
                client = await _connect(link)
                sub = await client.create_subscription(link["interval_ms"], Handler())
                await sub.subscribe_data_change([client.get_node(n["node_id"]) for n in link["nodes"]])
                self._set(lid, "connected")
                attempt = 0
                while True:
                    await asyncio.sleep(1)
                    await client.check_connection()
                    if self_status[0]:
                        self._set(lid, "connected", self_status[0])
                        self_status[0] = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._set(lid, "error", f"{type(exc).__name__}: {exc}"[:300])
            finally:
                if client is not None:
                    try:
                        await asyncio.shield(client.disconnect())
                    except BaseException:
                        pass
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            await asyncio.sleep(delay)
