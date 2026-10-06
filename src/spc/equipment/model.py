"""The record of an equipment link: one OPC UA server and the nodes that feed monitors."""

from __future__ import annotations

import math
import re
from typing import Any

from spc.monitor.model import ATTRIBUTE_KINDS, VECTOR_KINDS, MS_KIND, ZMR_KIND

UNSUPPORTED_KINDS = (*ATTRIBUTE_KINDS, *VECTOR_KINDS, MS_KIND, ZMR_KIND)  # these need more than a plain list of numbers
MAX_NODES = 50
NODE_ID = re.compile(r"^(ns=\d{1,5};)?[isgb]=[^\s;].{0,198}$")
SECURITY = re.compile(r"^(Basic256Sha256|Aes128_Sha256_RsaOaep|Aes256_Sha256_RsaPss),(Sign|SignAndEncrypt),[^,\s]+,[^,\s]+(,[^,\s]+)?$")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
CONFIG_KEYS = ("name", "description", "endpoint", "security", "username", "password_env", "interval_ms", "stale_after_s", "nodes")


class EquipmentError(ValueError):
    def __init__(self, code: str, message: str = "", status: int = 400, **params):
        super().__init__(message or code)
        self.code, self.status, self.params = code, status, params


def _text(d: dict, key: str, limit: int) -> str:
    v = d.get(key, "")
    if not isinstance(v, str) or len(v) > limit:
        raise ValueError(f"{key} must be text of at most {limit} characters")
    return v.strip()


def _int(d: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{key} must be a whole number from {lo} to {hi}")
    return v


def _number(v, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{name} must be a number")
    return float(v)


def validate_link(data: dict[str, Any]) -> dict:
    if not isinstance(data, dict) or set(data) - set(CONFIG_KEYS):
        raise ValueError(f"unknown setting(s): {sorted(set(data) - set(CONFIG_KEYS)) if isinstance(data, dict) else 'not an object'}")
    name = _text(data, "name", 100)
    if not name:
        raise ValueError("name must not be empty")
    endpoint = _text(data, "endpoint", 300)
    if not re.fullmatch(r"opc\.tcp://[^\s/]+(/\S*)?", endpoint):
        raise ValueError("endpoint must look like opc.tcp://host:4840/path")
    security = _text(data, "security", 300) or "none"
    if security != "none" and not SECURITY.match(security):
        raise ValueError("security must be 'none' or Policy,Mode,client-certificate,client-key[,server-certificate] (Mode: Sign or SignAndEncrypt)")
    username, env = _text(data, "username", 100), _text(data, "password_env", 64)
    if username and not ENV_NAME.match(env):
        raise ValueError("a user name needs password_env: the NAME of the environment variable that holds the password (the password itself is never stored)")
    if env and not ENV_NAME.match(env):
        raise ValueError("password_env must be an environment variable name such as OPC_PASSWORD")
    nodes_in = data.get("nodes")
    if not isinstance(nodes_in, list) or not 1 <= len(nodes_in) <= MAX_NODES:
        raise ValueError(f"nodes must list 1 to {MAX_NODES} nodes")
    nodes, seen_nodes, seen_monitors = [], set(), set()
    for i, n in enumerate(nodes_in, start=1):
        if not isinstance(n, dict) or set(n) - {"node_id", "monitor_id", "scale", "offset"}:
            raise ValueError(f"node {i}: use node_id, monitor_id, scale and offset")
        node_id = n.get("node_id")
        if not isinstance(node_id, str) or not NODE_ID.match(node_id.strip()):
            raise ValueError(f"node {i}: node_id must look like ns=2;s=Station1.Bore")
        mid = n.get("monitor_id")
        if isinstance(mid, bool) or not isinstance(mid, int) or mid < 1:
            raise ValueError(f"node {i}: monitor_id must be the id of a monitor")
        scale, offset = _number(n.get("scale", 1.0), f"node {i}: scale"), _number(n.get("offset", 0.0), f"node {i}: offset")
        if scale == 0:
            raise ValueError(f"node {i}: scale must not be 0")
        if node_id.strip() in seen_nodes or mid in seen_monitors:
            raise ValueError(f"node {i}: a node and a monitor can be used once per link")
        seen_nodes.add(node_id.strip()); seen_monitors.add(mid)
        nodes.append({"node_id": node_id.strip(), "monitor_id": mid, "scale": scale, "offset": offset})
    return {"name": name, "description": _text(data, "description", 500), "endpoint": endpoint, "security": security, "username": username, "password_env": env,
            "interval_ms": _int(data, "interval_ms", 1000, 100, 60000), "stale_after_s": _int(data, "stale_after_s", 0, 0, 86400), "nodes": nodes}
