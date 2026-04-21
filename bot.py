#!/usr/bin/env python3
"""
oct-bridge — Octra to Ethereum bridge utility.

Locks OCT on the Octra network and mints the equivalent wOCT on Ethereum
via a Merkle-proof-based verifyAndMint call.

Usage:
    python bot.py --amount <OCT> --wait-header 1800 --send
    python bot.py --tx <lock_tx_hash> --send
    python bot.py --all --wait-header 1800 --send
    python bot.py --tx <lock_tx_hash> --auto-claim-after-reset

Requirements:
    pip install web3 requests eth-abi pynacl
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any

import requests
from eth_abi import encode
from nacl.signing import SigningKey
from web3 import Web3


ETH_RPC    = os.getenv("ETH_RPC",        "https://ethereum-rpc.publicnode.com")
OCTRA_RPC  = os.getenv("OCTRA_RPC",      "https://octrascan.io/rpc")
PRIVATE_KEY = os.getenv("ETH_PRIVATE_KEY", "0xYOUR_PRIVATE_KEY_HERE")

OCTRA_BRIDGE_VAULT = os.getenv(
    "OCTRA_BRIDGE_VAULT",
    "oct5MrNfjiXFNRDLwsodn8Zm9hDKNGAYt3eQDCQ52bSpCHq",
)
ETHEREUM_BRIDGE = Web3.to_checksum_address(
    os.getenv("ETHEREUM_BRIDGE", "0xE7eD69b852fd2a1406080B26A37e8E04e7dA4caE")
)
OCTRA_LIGHT_CLIENT = Web3.to_checksum_address(
    os.getenv("OCTRA_LIGHT_CLIENT", "0xC01cA57dc7f7C4B6f1B6b87B85D79e5ddf0dF55d")
)
W_OCT = Web3.to_checksum_address(
    os.getenv("WOCT_TOKEN", "0x4647e1fE715c9e23959022C2416C71867F5a6E80")
)

ZERO32        = b"\x00" * 32
LABEL_MESSAGE = b"octra:bridge_message:v1\x00"
LABEL_LEAF    = b"octra:bridge_leaf:v1\x00"
LABEL_NODE    = b"octra:bridge_node:v1\x00"

OCT_DECIMALS    = 6
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

BRIDGE_MESSAGE_TYPES = [
    "uint8", "uint8", "uint64", "uint64",
    "bytes32", "bytes32", "bytes32",
    "address", "uint128", "uint64",
]

_MSG_COMPONENTS = [
    {"internalType": "uint8",   "name": "version",     "type": "uint8"},
    {"internalType": "uint8",   "name": "direction",   "type": "uint8"},
    {"internalType": "uint64",  "name": "srcChainId",  "type": "uint64"},
    {"internalType": "uint64",  "name": "dstChainId",  "type": "uint64"},
    {"internalType": "bytes32", "name": "srcBridgeId", "type": "bytes32"},
    {"internalType": "bytes32", "name": "dstBridgeId", "type": "bytes32"},
    {"internalType": "bytes32", "name": "tokenId",     "type": "bytes32"},
    {"internalType": "address", "name": "recipient",   "type": "address"},
    {"internalType": "uint128", "name": "amount",      "type": "uint128"},
    {"internalType": "uint64",  "name": "srcNonce",    "type": "uint64"},
]

BRIDGE_ABI = [
    {"inputs": [], "name": "BRIDGE_VERSION",  "outputs": [{"internalType": "uint8",   "name": "", "type": "uint8"}],   "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "DIRECTION_O2E",   "outputs": [{"internalType": "uint8",   "name": "", "type": "uint8"}],   "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "OCTRA_CHAIN_ID",  "outputs": [{"internalType": "uint64",  "name": "", "type": "uint64"}],  "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "ETH_CHAIN_ID",    "outputs": [{"internalType": "uint64",  "name": "", "type": "uint64"}],  "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "SRC_BRIDGE_ID",   "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "DST_BRIDGE_ID",   "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "TOKEN_ID_OCT",    "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "mintCapPerTx",    "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "mintCapDaily",    "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "mintedToday",     "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "paused",          "outputs": [{"internalType": "bool",    "name": "", "type": "bool"}],    "stateMutability": "view", "type": "function"},
    {
        "inputs":  [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "name":    "processedMessages",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs":  [{"components": _MSG_COMPONENTS, "internalType": "struct EthereumBridge.BridgeMessageV1", "name": "m", "type": "tuple"}],
        "name":    "hashBridgeLeaf",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "pure",
        "type": "function",
    },
    {
        "inputs":  [{"components": _MSG_COMPONENTS, "internalType": "struct EthereumBridge.BridgeMessageV1", "name": "m", "type": "tuple"}],
        "name":    "hashBridgeMessage",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "pure",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "left",  "type": "bytes32"},
            {"internalType": "bytes32", "name": "right", "type": "bytes32"},
        ],
        "name":    "hashBridgeNode",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "pure",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint64",    "name": "epochId",   "type": "uint64"},
            {"components": _MSG_COMPONENTS, "internalType": "struct EthereumBridge.BridgeMessageV1", "name": "m", "type": "tuple"},
            {"internalType": "bytes32[]", "name": "siblings",  "type": "bytes32[]"},
            {"internalType": "uint32",    "name": "leafIndex", "type": "uint32"},
        ],
        "name":    "verifyAndMint",
        "outputs": [{"internalType": "bytes32", "name": "messageId", "type": "bytes32"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "internalType": "bytes32", "name": "messageId", "type": "bytes32"},
            {"indexed": True,  "internalType": "uint64",  "name": "epochId",   "type": "uint64"},
            {"indexed": True,  "internalType": "address", "name": "recipient", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount",    "type": "uint256"},
        ],
        "name": "MintFinalized",
        "type": "event",
    },
]

LIGHT_CLIENT_ABI = [
    {
        "inputs":  [{"internalType": "uint64", "name": "epochId", "type": "uint64"}],
        "name":    "bridgeRootOf",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs":  [],
        "name":    "latestEpoch",
        "outputs": [{"internalType": "uint64", "name": "", "type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class BridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class LockMessage:
    tx_hash:    str
    sender:     str
    recipient:  str
    amount_raw: int
    src_nonce:  int
    epoch:      int
    timestamp:  float


@dataclass(frozen=True)
class BridgeProof:
    ok:         bool
    strategy:   str
    leaf_index: int
    siblings:   list[bytes]
    root:       bytes


class OctraRpc:
    def __init__(self, url: str) -> None:
        self.url = url

    def call(self, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            resp = requests.post(self.url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise BridgeError(f"Octra RPC request failed [{method}]: {exc}") from exc
        if data.get("error"):
            raise BridgeError(f"Octra RPC error [{method}]: {data['error']}")
        return data.get("result")


def load_env_file(path: str) -> None:
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
                lk = key.lower()
                if lk == "priv octra"  and "OCTRA_PRIVATE_KEY"    not in os.environ and val:
                    os.environ["OCTRA_PRIVATE_KEY"] = val
                if lk == "address evm" and "BRIDGE_EVM_RECIPIENT" not in os.environ and val:
                    os.environ["BRIDGE_EVM_RECIPIENT"] = val
                if lk == "priv evm"    and "ETH_PRIVATE_KEY"      not in os.environ and val:
                    os.environ["ETH_PRIVATE_KEY"] = val if val.startswith("0x") else "0x" + val
            elif ":" in line:
                key, val = line.split(":", 1)
                key = key.strip().lower()
                val = val.strip().strip('"').strip("'")
                if key == "priv evm"    and "ETH_PRIVATE_KEY"      not in os.environ and val:
                    os.environ["ETH_PRIVATE_KEY"] = val if val.startswith("0x") else "0x" + val
                if key == "address evm" and "BRIDGE_EVM_RECIPIENT" not in os.environ and val:
                    os.environ["BRIDGE_EVM_RECIPIENT"] = val
                if key == "priv octra"  and "OCTRA_PRIVATE_KEY"    not in os.environ and val:
                    os.environ["OCTRA_PRIVATE_KEY"] = val


def normalize_octra_tx_hash(tx_hash: str) -> str:
    value = tx_hash.lower().removeprefix("0x")
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise BridgeError("tx hash must be 64 hex characters (with or without 0x prefix)")
    return value


def raw_to_oct(amount_raw: int) -> str:
    scale = Decimal(10) ** OCT_DECIMALS
    return format(
        (Decimal(amount_raw) / scale).quantize(Decimal("0.000001"), rounding=ROUND_DOWN), "f"
    )


def parse_oct_amount_raw(value: str) -> int:
    text = (value or "").strip()
    if not text:
        raise BridgeError("amount is required")
    if text.count(".") > 1:
        raise BridgeError("invalid amount format")
    try:
        amount = Decimal(text)
    except Exception as exc:
        raise BridgeError("invalid amount") from exc
    if amount <= 0:
        raise BridgeError("amount must be greater than zero")
    scale       = Decimal(10) ** OCT_DECIMALS
    raw_decimal = amount * scale
    raw_int     = raw_decimal.to_integral_value(rounding=ROUND_DOWN)
    if raw_decimal != raw_int:
        raise BridgeError("amount supports a maximum of 6 decimal places")
    return int(raw_int)


def parse_raw_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value.strip())
    raise BridgeError(f"cannot parse integer: {value!r}")


def json_escape(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]


def to_hex32(value: bytes) -> str:
    return "0x" + value.hex()


def json_ready(value: Any) -> Any:
    if isinstance(value, bytes):
        return to_hex32(value)
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, dict):
        return {k: json_ready(v) for k, v in value.items()}
    return value


def base58_encode(data: bytes) -> str:
    if not data:
        return ""
    num = int.from_bytes(data, "big")
    out = ""
    while num > 0:
        num, rem = divmod(num, 58)
        out = BASE58_ALPHABET[rem] + out
    pad = sum(1 for b in data if b == 0)
    return ("1" * pad) + (out or "1")


def derive_octra_address(public_key: bytes) -> str:
    body = base58_encode(hashlib.sha256(public_key).digest())
    while len(body) < 44:
        body = "1" + body
    return "oct" + body


def resolve_octra_private_key(arg_value: str | None) -> str:
    return arg_value or os.getenv("OCTRA_PRIVATE_KEY", "")


def decode_octra_private_key(private_key_b64: str) -> tuple[bytes, bytes]:
    clean = "".join(ch for ch in private_key_b64 if ch not in " \t\r\n")
    if not clean:
        raise BridgeError("OCTRA_PRIVATE_KEY is not set — use .env or pass --octra-private-key")
    try:
        raw = base64.b64decode(clean, validate=True)
    except Exception as exc:
        raise BridgeError("Octra private key must be valid base64") from exc
    if len(raw) >= 64:
        seed = raw[:32]
        sk   = SigningKey(seed)
        vk   = bytes(sk.verify_key)
        if raw[32:64] != vk:
            raise BridgeError("Octra private key has an inconsistent embedded public key")
        return seed, vk
    if len(raw) >= 32:
        sk = SigningKey(raw[:32])
        return raw[:32], bytes(sk.verify_key)
    raise BridgeError("Octra private key must decode to at least 32 bytes")


def derive_octra_account(private_key_b64: str) -> dict[str, Any]:
    seed, public_key = decode_octra_private_key(private_key_b64)
    sk = SigningKey(seed)
    return {
        "signing_key":    sk,
        "seed_b64":       base64.b64encode(seed).decode(),
        "public_key":     public_key,
        "public_key_b64": base64.b64encode(public_key).decode(),
        "address":        derive_octra_address(public_key),
    }


def build_octra_canonical_json(tx: dict[str, Any]) -> str:
    op_type = tx.get("op_type") or "standard"
    payload = (
        "{\"from\":\""      + json_escape(tx["from"])   + "\""
        + ",\"to_\":\""     + json_escape(tx["to_"])    + "\""
        + ",\"amount\":\""  + json_escape(tx["amount"]) + "\""
        + ",\"nonce\":"     + str(int(tx["nonce"]))
        + ",\"ou\":\""      + json_escape(tx["ou"])     + "\""
        + ",\"timestamp\":" + json.dumps(float(tx["timestamp"]), ensure_ascii=False, separators=(",", ":"))
        + ",\"op_type\":\"" + json_escape(op_type)      + "\""
    )
    if tx.get("encrypted_data"):
        payload += ",\"encrypted_data\":\"" + json_escape(tx["encrypted_data"]) + "\""
    if tx.get("message"):
        payload += ",\"message\":\"" + json_escape(tx["message"]) + "\""
    return payload + "}"


def sign_octra_transaction(tx: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    sig    = account["signing_key"].sign(build_octra_canonical_json(tx).encode()).signature
    signed = dict(tx)
    signed["signature"]  = base64.b64encode(sig).decode()
    signed["public_key"] = account["public_key_b64"]
    return signed


def parse_balance_raw(payload: dict[str, Any]) -> int:
    if "balance_raw" in payload:
        return parse_raw_int(payload["balance_raw"])
    if "balance" in payload:
        return parse_oct_amount_raw(str(payload["balance"]))
    return 0


def get_nonce_balance(octra: OctraRpc, address: str) -> tuple[int, int]:
    data = octra.call("octra_balance", [address])
    if not isinstance(data, dict):
        raise BridgeError("octra_balance returned an unexpected payload")
    nonce       = int(data.get("pending_nonce", data.get("nonce", 0)))
    balance_raw = parse_balance_raw(data)
    try:
        staging = octra.call("staging_view", [])
        if isinstance(staging, dict):
            for tx in staging.get("transactions", []):
                if tx.get("from") == address:
                    n = int(tx.get("nonce", 0))
                    if n > nonce:
                        nonce = n
    except BridgeError:
        pass
    return nonce, balance_raw


def wait_for_lock_receipt(
    octra: OctraRpc, tx_hash: str, vault: str, wait_lock: int, poll: int,
) -> LockMessage:
    deadline   = time.time() + max(wait_lock, 0)
    last_error = ""
    while True:
        try:
            return fetch_lock_receipt(octra, tx_hash, vault)
        except BridgeError as exc:
            last_error = str(exc)
            if wait_lock <= 0 or time.time() >= deadline:
                raise BridgeError(f"lock was submitted but receipt is not yet available: {last_error}") from exc
            time.sleep(max(poll, 1))


def submit_octra_lock(
    octra_rpc_url: str,
    octra_bridge_vault: str,
    octra_private_key: str,
    evm_recipient: str,
    amount_arg: str,
    all_balance: bool,
    ou: str,
    wait_lock: int,
    poll: int,
    expected_octra_address: str = "",
) -> dict[str, Any]:
    if not Web3.is_address(evm_recipient):
        raise BridgeError("EVM recipient must be a valid Ethereum address (0x...)")
    account = derive_octra_account(octra_private_key)
    if expected_octra_address and expected_octra_address != account["address"]:
        raise BridgeError("--octra-address does not match the address derived from the private key")
    octra = OctraRpc(octra_rpc_url)
    nonce, balance_raw = get_nonce_balance(octra, account["address"])
    ou_raw = parse_raw_int(ou)
    if ou_raw < 0:
        raise BridgeError("fee (--ou) must be >= 0")
    if all_balance:
        amount_raw = balance_raw - ou_raw
        if amount_raw <= 0:
            raise BridgeError("insufficient OCT balance after deducting the fee")
    else:
        amount_raw = parse_oct_amount_raw(amount_arg)
        if amount_raw + ou_raw > balance_raw:
            raise BridgeError(
                f"insufficient OCT balance: {raw_to_oct(amount_raw + ou_raw)} OCT required (including fee)"
            )
    tx = {
        "from":           account["address"],
        "to_":            octra_bridge_vault,
        "amount":         str(amount_raw),
        "nonce":          nonce + 1,
        "ou":             str(ou_raw),
        "timestamp":      time.time(),
        "op_type":        "call",
        "encrypted_data": "lock_to_eth",
        "message":        json.dumps([Web3.to_checksum_address(evm_recipient)], separators=(",", ":")),
    }
    signed  = sign_octra_transaction(tx, account)
    payload = {k: signed[k] for k in (
        "from", "to_", "amount", "nonce", "ou", "timestamp",
        "signature", "public_key", "op_type", "encrypted_data", "message",
    )}
    result = octra.call("octra_submit", [payload])
    if not isinstance(result, dict):
        raise BridgeError("octra_submit returned an unexpected payload")
    tx_hash = result.get("tx_hash") or hashlib.sha256(build_octra_canonical_json(tx).encode()).hexdigest()
    tx_hash = normalize_octra_tx_hash(tx_hash)
    receipt = wait_for_lock_receipt(octra, tx_hash, octra_bridge_vault, wait_lock, poll)
    return {
        "tx_hash":            tx_hash,
        "octra_sender":       account["address"],
        "recipient":          receipt.recipient,
        "amount_raw":         str(receipt.amount_raw),
        "amount_oct":         raw_to_oct(receipt.amount_raw),
        "src_nonce":          receipt.src_nonce,
        "epoch":              receipt.epoch,
        "lock_nonce":         int(tx["nonce"]),
        "lock_ou":            str(ou_raw),
        "lock_timestamp":     float(tx["timestamp"]),
        "octra_bridge_vault": octra_bridge_vault,
    }


def parse_lock_receipt(tx_hash: str, receipt: dict[str, Any], vault: str) -> LockMessage:
    if receipt.get("method") != "lock_to_eth":
        raise BridgeError("transaction is not a lock_to_eth receipt")
    contract = receipt.get("contract")
    if contract and contract != vault:
        raise BridgeError(f"receipt contract {contract!r} does not match vault {vault!r}")
    if not receipt.get("success", False):
        raise BridgeError("lock transaction was not successful")
    for event in receipt.get("events", []):
        if event.get("event") != "Locked":
            continue
        vals = event.get("values", [])
        if len(vals) < 4:
            continue
        recipient = vals[2]
        if not Web3.is_address(recipient):
            raise BridgeError(f"invalid Ethereum recipient in lock event: {recipient}")
        return LockMessage(
            tx_hash=tx_hash,
            sender=str(vals[0]),
            recipient=Web3.to_checksum_address(recipient),
            amount_raw=int(vals[1]),
            src_nonce=int(vals[3]),
            epoch=int(receipt["epoch"]),
            timestamp=float(receipt.get("ts", 0.0)),
        )
    raise BridgeError("Locked event not found in transaction receipt")


def fetch_lock_receipt(octra: OctraRpc, tx_hash: str, vault: str) -> LockMessage:
    receipt = octra.call("contract_receipt", [tx_hash])
    if not isinstance(receipt, dict):
        raise BridgeError("contract_receipt returned an unexpected payload")
    return parse_lock_receipt(tx_hash, receipt, vault)


def fetch_epoch_lock_messages(octra: OctraRpc, epoch: int, vault: str) -> list[LockMessage]:
    messages: list[LockMessage] = []
    offset = 0
    while True:
        page = octra.call("octra_transactionsByEpoch", [epoch, 100, offset])
        if not isinstance(page, dict):
            break
        txs = page.get("transactions") or []
        if not txs:
            break
        for tx in txs:
            if tx.get("to") != vault:
                continue
            if tx.get("op_type") != "call" or tx.get("encrypted_data") != "lock_to_eth":
                continue
            h = tx.get("hash")
            if not h:
                continue
            try:
                messages.append(fetch_lock_receipt(octra, h, vault))
            except BridgeError:
                continue
        if not page.get("has_more") or len(txs) < 100:
            break
        offset += len(txs)
    if not messages:
        raise BridgeError(f"no bridge lock messages found in epoch {epoch}")
    return messages


def read_bridge_constants(contract: Any) -> dict[str, Any]:
    return {
        "BRIDGE_VERSION": contract.functions.BRIDGE_VERSION().call(),
        "DIRECTION_O2E":  contract.functions.DIRECTION_O2E().call(),
        "OCTRA_CHAIN_ID": contract.functions.OCTRA_CHAIN_ID().call(),
        "ETH_CHAIN_ID":   contract.functions.ETH_CHAIN_ID().call(),
        "SRC_BRIDGE_ID":  contract.functions.SRC_BRIDGE_ID().call(),
        "DST_BRIDGE_ID":  contract.functions.DST_BRIDGE_ID().call(),
        "TOKEN_ID_OCT":   contract.functions.TOKEN_ID_OCT().call(),
        "mintCapPerTx":   contract.functions.mintCapPerTx().call(),
        "mintCapDaily":   contract.functions.mintCapDaily().call(),
        "mintedToday":    contract.functions.mintedToday().call(),
        "paused":         contract.functions.paused().call(),
    }


def build_bridge_message(constants: dict[str, Any], lock: LockMessage) -> tuple[Any, ...]:
    return (
        int(constants["BRIDGE_VERSION"]),
        int(constants["DIRECTION_O2E"]),
        int(constants["OCTRA_CHAIN_ID"]),
        int(constants["ETH_CHAIN_ID"]),
        bytes(constants["SRC_BRIDGE_ID"]),
        bytes(constants["DST_BRIDGE_ID"]),
        bytes(constants["TOKEN_ID_OCT"]),
        Web3.to_checksum_address(lock.recipient),
        int(lock.amount_raw),
        int(lock.src_nonce),
    )


def encode_bridge_message_payload(message: tuple[Any, ...]) -> bytes:
    return encode(BRIDGE_MESSAGE_TYPES, list(message))


def sha256_prefixed(label: bytes, payload: bytes) -> bytes:
    return hashlib.sha256(label + payload).digest()


def hash_bridge_message(message: tuple[Any, ...]) -> bytes:
    return sha256_prefixed(LABEL_MESSAGE, encode_bridge_message_payload(message))


def hash_bridge_leaf(message: tuple[Any, ...]) -> bytes:
    return sha256_prefixed(LABEL_LEAF, encode_bridge_message_payload(message))


def hash_bridge_node(left: bytes, right: bytes) -> bytes:
    return sha256_prefixed(LABEL_NODE, left + right)


def build_bridge_proof_candidate(
    leaves: list[bytes], target_index: int, label: str, duplicate_last: bool,
) -> BridgeProof:
    if not leaves or target_index >= len(leaves):
        return BridgeProof(False, label, 0, [], ZERO32)
    siblings: list[bytes] = []
    layer    = list(leaves)
    idx      = target_index
    strategy = f"{label}|{'duplicate-last' if duplicate_last else 'promote-last'}"
    while len(layer) > 1:
        next_layer: list[bytes] = []
        for i in range(0, len(layer), 2):
            if i + 1 >= len(layer):
                if duplicate_last:
                    node = hash_bridge_node(layer[i], layer[i])
                    next_layer.append(node)
                    if idx == i:
                        siblings.append(layer[i])
                        idx = len(next_layer) - 1
                else:
                    next_layer.append(layer[i])
                    if idx == i:
                        idx = len(next_layer) - 1
                continue
            node = hash_bridge_node(layer[i], layer[i + 1])
            next_layer.append(node)
            if idx == i:
                siblings.append(layer[i + 1])
                idx = len(next_layer) - 1
            elif idx == i + 1:
                siblings.append(layer[i])
                idx = len(next_layer) - 1
        layer = next_layer
    return BridgeProof(True, strategy, target_index, siblings, layer[0])


def build_bridge_proof(
    messages: list[LockMessage],
    target_tx_hash: str,
    expected_root: bytes,
    constants: dict[str, Any],
) -> BridgeProof:
    if not messages:
        raise BridgeError("bridge epoch contains no messages")
    base = [
        {
            "tx_hash":   m.tx_hash,
            "src_nonce": m.src_nonce,
            "timestamp": m.timestamp,
            "leaf":      hash_bridge_leaf(build_bridge_message(constants, m)),
        }
        for m in messages
    ]
    orders = [
        ("epoch-order",   lambda rows: rows),
        ("nonce-asc",     lambda rows: sorted(rows, key=lambda r: (r["src_nonce"], r["tx_hash"]))),
        ("timestamp-asc", lambda rows: sorted(rows, key=lambda r: (r["timestamp"],  r["tx_hash"]))),
        ("txhash-asc",    lambda rows: sorted(rows, key=lambda r: r["tx_hash"])),
    ]
    for order_name, sorter in orders:
        rows   = sorter(list(base))
        leaves = [r["leaf"] for r in rows]
        ti     = {r["tx_hash"]: i for i, r in enumerate(rows)}.get(target_tx_hash)
        if ti is None:
            continue
        for dup in (False, True):
            proof = build_bridge_proof_candidate(leaves, ti, order_name, dup)
            if proof.ok and proof.root == expected_root:
                return proof
    raise BridgeError("could not reconstruct a proof matching bridgeRootOf(epoch)")


def wait_for_bridge_root(
    light_client: Any, epoch: int, wait_header: int, poll: int,
) -> tuple[bytes, int]:
    latest_epoch = 0
    deadline     = time.time() + max(wait_header, 0)
    while True:
        latest_epoch = int(light_client.functions.latestEpoch().call())
        root         = bytes(light_client.functions.bridgeRootOf(epoch).call())
        if root != ZERO32:
            return root, latest_epoch
        if wait_header <= 0 or time.time() >= deadline:
            return root, latest_epoch
        time.sleep(max(poll, 1))


def build_fee_params(w3: Web3) -> dict[str, int]:
    latest   = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas")
    if base_fee is not None:
        try:
            priority = int(w3.eth.max_priority_fee)
        except Exception:
            priority = int(w3.to_wei(2, "gwei"))
        return {"maxPriorityFeePerGas": priority, "maxFeePerGas": int(base_fee * 2 + priority)}
    return {"gasPrice": int(w3.eth.gas_price * 115 // 100)}


def resolve_private_key(arg_value: str | None) -> str:
    return arg_value or os.getenv("ETH_PRIVATE_KEY", PRIVATE_KEY)


def inspect_bridge(
    tx_hash: str, octra_rpc_url: str, eth_rpc_url: str,
    octra_bridge_vault: str, ethereum_bridge: str, light_client_addr: str,
) -> tuple[dict[str, Any], Web3, Any]:
    octra          = OctraRpc(octra_rpc_url)
    lock           = fetch_lock_receipt(octra, tx_hash, octra_bridge_vault)
    epoch_messages = fetch_epoch_lock_messages(octra, lock.epoch, octra_bridge_vault)
    w3 = Web3(Web3.HTTPProvider(eth_rpc_url))
    if not w3.is_connected():
        raise BridgeError(f"unable to connect to Ethereum RPC: {eth_rpc_url}")
    bridge       = w3.eth.contract(address=Web3.to_checksum_address(ethereum_bridge),   abi=BRIDGE_ABI)
    light_client = w3.eth.contract(address=Web3.to_checksum_address(light_client_addr), abi=LIGHT_CLIENT_ABI)
    constants    = read_bridge_constants(bridge)
    message      = build_bridge_message(constants, lock)
    message_id   = hash_bridge_message(message)
    leaf         = hash_bridge_leaf(message)
    return (
        {
            "lock": lock, "epoch_messages": epoch_messages,
            "constants": constants, "message": message,
            "message_id": message_id, "leaf": leaf,
            "bridge_contract": bridge, "light_client": light_client,
        },
        w3, bridge,
    )


def next_utc_day_reset(epoch_seconds: int | None = None) -> int:
    now = int(time.time() if epoch_seconds is None else epoch_seconds)
    return ((now // 86400) + 1) * 86400


def parse_json_output(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeError(f"failed to parse subprocess JSON output: {exc}") from exc


def build_self_command(args: argparse.Namespace, send: bool) -> list[str]:
    cmd = [
        sys.executable, os.path.abspath(__file__),
        "--tx", args.tx,
        "--octra-rpc", args.octra_rpc,
        "--eth-rpc", args.eth_rpc,
        "--octra-bridge-vault", args.octra_bridge_vault,
        "--ethereum-bridge", args.ethereum_bridge,
        "--light-client", args.light_client,
        "--json",
    ]
    if args.private_key:
        cmd.extend(["--private-key", args.private_key])
    if send:
        cmd.append("--send")
    return cmd


def run_auto_claim_after_reset(args: argparse.Namespace) -> int:
    reset_ts = next_utc_day_reset()
    if not args.json:
        print(f"Waiting for UTC daily reset at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(reset_ts))}")
    while time.time() < reset_ts:
        time.sleep(min(args.poll, max(1, reset_ts - int(time.time()))))
    while True:
        ir = subprocess.run(build_self_command(args, False), capture_output=True, text=True, check=False)
        id = parse_json_output(ir.stdout.strip())
        st = id.get("status", id.get("error", ""))
        if st == "already_processed":
            print(json.dumps(id, indent=2) if args.json else "Claim already processed.")
            return 0
        if st == "ready_to_send":
            sr = subprocess.run(build_self_command(args, True), capture_output=True, text=True, check=False)
            sd = parse_json_output(sr.stdout.strip())
            print(json.dumps(sd, indent=2) if args.json else sd.get("status", "unknown"))
            if sd.get("status") in {"submitted", "already_processed"}:
                return 0
        if args.json:
            print(json.dumps(id, indent=2))
        else:
            print(f"  Status: {st} — retrying in {args.poll}s")
        time.sleep(args.poll)


_STATUS_LABEL = {
    "submitted":         "[ OK ] Submitted",
    "already_processed": "[ OK ] Already processed",
    "ready_to_send":     "[    ] Ready to send",
    "waiting_for_header":"[...] Waiting for header",
    "simulation_failed": "[ERR] Simulation failed",
    "lock_submitted":    "[LCK] Lock submitted",
    "reverted":          "[ERR] Reverted",
}


def print_human(result: dict[str, Any]) -> None:
    LINE  = "─" * 62
    DLINE = "═" * 62

    def row(label: str, value: Any) -> None:
        print(f"  {label:<22} {value}")

    status = result.get("status", "")

    if status == "lock_submitted" and "ethereum_bridge" not in result:
        print(f"\n{DLINE}")
        print("  OCT LOCK SUBMITTED")
        print(DLINE)
        if result.get("lock_submitted"):
            row("Sender",        result["octra_sender"])
            row("Nonce",         result["lock_nonce"])
            row("Fee (raw)",     result["lock_ou"])
            print(LINE)
        row("Octra TX",     result["tx_hash"])
        row("Recipient",    result["recipient"])
        row("Amount",       f"{result['amount_oct']} OCT")
        row("Epoch",        result["epoch"])
        row("Status",       _STATUS_LABEL.get(status, status))
        print(f"{DLINE}\n")
        return

    print(f"\n{DLINE}")
    print("  OCT → wOCT  |  Octra to Ethereum Bridge")
    print(DLINE)

    if result.get("lock_submitted"):
        row("Sender",      result["octra_sender"])
        row("Lock nonce",  result["lock_nonce"])
        row("Fee (raw)",   result["lock_ou"])
        print(LINE)

    row("Octra TX",        result["tx_hash"])
    row("Recipient",       result["recipient"])
    row("Amount",          f"{result['amount_oct']} OCT  [{result['amount_raw']} raw]")
    row("Epoch",           result["epoch"])
    row("Source nonce",    result["src_nonce"])
    print(LINE)

    row("Header available", "Yes" if result["header_available"] else "No")
    row("Latest ETH epoch", result["latest_eth_epoch"])
    row("Already processed", "Yes" if result["processed"] else "No")

    if result.get("proof_strategy"):
        row("Proof strategy", result["proof_strategy"])
        row("Siblings",       len(result["siblings"]))

    sim = result.get("simulation_ok")
    if sim is True:
        row("Simulation", "Passed")
    elif sim is False:
        row("Simulation", "Failed")
        if result.get("simulation_error"):
            row("Sim. error", result["simulation_error"])

    print(LINE)
    row("Status", _STATUS_LABEL.get(status, status))

    if result.get("eth_tx_hash"):
        row("Ethereum TX", result["eth_tx_hash"])

    if result.get("mint_finalized"):
        for mint in result["mint_finalized"]:
            row("wOCT minted", f"{mint['amount_oct']} wOCT  →  {mint['recipient']}")

    print(f"{DLINE}\n")


def main(argv: list[str] | None = None) -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env-file")
    pre_args, _ = pre.parse_known_args(argv)
    script_dir  = os.path.dirname(os.path.abspath(__file__))
    load_env_file(pre_args.env_file or os.path.join(script_dir, ".env"))

    parser = argparse.ArgumentParser(
        prog="bot.py",
        description="Bridge OCT to wOCT via Octra lock and Ethereum verifyAndMint.",
    )

    g = parser.add_argument_group("Lock")
    g.add_argument("--amount",           help="OCT amount to lock (up to 6 decimal places)")
    g.add_argument("--all",              action="store_true", help="lock the full balance minus the fee")
    g.add_argument("--evm-recipient",    default=os.getenv("BRIDGE_EVM_RECIPIENT", ""), help="Ethereum address to receive wOCT")
    g.add_argument("--octra-private-key", help="base64-encoded Octra private key")
    g.add_argument("--octra-address",    default=os.getenv("OCTRA_ADDRESS", ""), help="verify derived Octra address matches this value")
    g.add_argument("--ou",               default="1000", help="Octra fee in raw units (default: 1000)")

    g = parser.add_argument_group("Claim")
    g.add_argument("--tx",          default="", help="existing Octra lock tx hash to claim")
    g.add_argument("--private-key",             help="0x-prefixed Ethereum private key")

    g = parser.add_argument_group("Network")
    g.add_argument("--octra-rpc",          default=os.getenv("OCTRA_RPC",          OCTRA_RPC))
    g.add_argument("--eth-rpc",            default=os.getenv("ETH_RPC",            ETH_RPC))
    g.add_argument("--octra-bridge-vault", default=os.getenv("OCTRA_BRIDGE_VAULT", OCTRA_BRIDGE_VAULT))
    g.add_argument("--ethereum-bridge",    default=os.getenv("ETHEREUM_BRIDGE",    str(ETHEREUM_BRIDGE)))
    g.add_argument("--light-client",       default=os.getenv("OCTRA_LIGHT_CLIENT", str(OCTRA_LIGHT_CLIENT)))

    g = parser.add_argument_group("Timing")
    g.add_argument("--wait-lock",   type=int, default=120, help="seconds to wait for lock receipt (default: 120)")
    g.add_argument("--wait-header", type=int, default=0,   help="seconds to wait for bridge header (default: 0)")
    g.add_argument("--poll",        type=int, default=15,  help="polling interval in seconds (default: 15)")

    g = parser.add_argument_group("Options")
    g.add_argument("--send",                   action="store_true", help="submit verifyAndMint after simulation passes")
    g.add_argument("--lock-only",              action="store_true", help="submit lock and exit without claiming")
    g.add_argument("--auto-claim-after-reset", action="store_true", help="wait for UTC reset and retry until claim succeeds")
    g.add_argument("--json",                   action="store_true", help="output results as JSON")
    g.add_argument("--env-file",                                    help="path to a custom .env file")

    args = parser.parse_args(argv)

    if args.auto_claim_after_reset and not args.tx:
        parser.error("--auto-claim-after-reset requires --tx")

    if args.auto_claim_after_reset:
        return run_auto_claim_after_reset(args)

    try:
        if args.tx and (args.amount or args.all):
            raise BridgeError("specify either --tx or --amount/--all, not both")
        if args.all and args.amount:
            raise BridgeError("specify either --all or --amount, not both")

        lock_result: dict[str, Any] | None = None
        tx_hash = ""

        if args.amount or args.all:
            evm_recipient = args.evm_recipient or os.getenv("BRIDGE_EVM_RECIPIENT", "")
            if not evm_recipient:
                raise BridgeError("EVM recipient is required — set BRIDGE_EVM_RECIPIENT in .env or pass --evm-recipient")
            lock_result = submit_octra_lock(
                octra_rpc_url=args.octra_rpc,
                octra_bridge_vault=args.octra_bridge_vault,
                octra_private_key=resolve_octra_private_key(args.octra_private_key),
                evm_recipient=evm_recipient,
                amount_arg=args.amount or "",
                all_balance=args.all,
                ou=args.ou,
                wait_lock=args.wait_lock,
                poll=args.poll,
                expected_octra_address=args.octra_address,
            )
            tx_hash = lock_result["tx_hash"]
            args.tx = tx_hash
            if args.lock_only:
                out = {**lock_result, "status": "lock_submitted"}
                print(json.dumps(json_ready(out), indent=2) if args.json else None) or print_human(json_ready(out))
                return 0
        elif args.tx:
            tx_hash = normalize_octra_tx_hash(args.tx)
        else:
            raise BridgeError("provide --tx to claim an existing lock, or --amount/--all to submit a new one")

        info, w3, bridge = inspect_bridge(
            tx_hash, args.octra_rpc, args.eth_rpc,
            args.octra_bridge_vault, args.ethereum_bridge, args.light_client,
        )
        lock         = info["lock"]
        constants    = info["constants"]
        message      = info["message"]
        message_id   = info["message_id"]
        leaf         = info["leaf"]
        light_client = info["light_client"]

        if constants["paused"]:
            raise BridgeError("the Ethereum bridge contract is currently paused")

        bridge_root, latest_eth_epoch = wait_for_bridge_root(light_client, lock.epoch, args.wait_header, args.poll)
        header_available = bridge_root != ZERO32
        processed        = bool(bridge.functions.processedMessages(message_id).call())

        result: dict[str, Any] = {
            "tx_hash":             tx_hash,
            "octra_rpc":           args.octra_rpc,
            "eth_rpc":             args.eth_rpc,
            "octra_bridge_vault":  args.octra_bridge_vault,
            "ethereum_bridge":     Web3.to_checksum_address(args.ethereum_bridge),
            "light_client":        Web3.to_checksum_address(args.light_client),
            "woct_token":          Web3.to_checksum_address(os.getenv("WOCT_TOKEN", W_OCT)),
            "epoch":               lock.epoch,
            "sender":              lock.sender,
            "recipient":           lock.recipient,
            "amount_raw":          str(lock.amount_raw),
            "amount_oct":          raw_to_oct(lock.amount_raw),
            "src_nonce":           lock.src_nonce,
            "epoch_message_count": len(info["epoch_messages"]),
            "message_id":          to_hex32(message_id),
            "leaf":                to_hex32(leaf),
            "bridge_root":         to_hex32(bridge_root),
            "header_available":    header_available,
            "latest_eth_epoch":    latest_eth_epoch,
            "processed":           processed,
            "mint_cap_per_tx_raw": str(constants["mintCapPerTx"]),
            "mint_cap_per_tx_oct": raw_to_oct(int(constants["mintCapPerTx"])),
            "minted_today_raw":    str(constants["mintedToday"]),
            "minted_today_oct":    raw_to_oct(int(constants["mintedToday"])),
            "simulation_ok":       None,
            "claim_ready":         False,
        }

        if lock_result:
            result.update({
                "lock_submitted": True,
                "octra_sender":   lock_result["octra_sender"],
                "lock_nonce":     lock_result["lock_nonce"],
                "lock_ou":        lock_result["lock_ou"],
                "lock_timestamp": lock_result["lock_timestamp"],
            })

        if lock.amount_raw > int(constants["mintCapPerTx"]):
            result["warning"] = "amount exceeds the per-transaction mint cap"

        if header_available:
            proof = build_bridge_proof(info["epoch_messages"], tx_hash, bridge_root, constants)
            result.update({
                "proof_strategy": proof.strategy,
                "leaf_index":     proof.leaf_index,
                "siblings":       [to_hex32(s) for s in proof.siblings],
            })
        else:
            result["status"] = "waiting_for_header"

        if processed:
            result["status"] = "already_processed"

        if header_available and not processed:
            siblings_bytes = [bytes.fromhex(s[2:]) for s in result["siblings"]]
            try:
                sim_id = bridge.functions.verifyAndMint(
                    lock.epoch, message, siblings_bytes, result["leaf_index"],
                ).call()
                result.update({
                    "simulation_ok":         True,
                    "simulation_message_id": to_hex32(bytes(sim_id)),
                    "claim_ready":           True,
                    "status":                "ready_to_send",
                })
            except Exception as exc:
                result.update({
                    "simulation_ok":    False,
                    "simulation_error": str(exc),
                    "status":           "simulation_failed",
                })

        if args.send:
            if not header_available:
                raise BridgeError("bridge header is not yet available on Ethereum")
            if processed:
                raise BridgeError("this bridge message has already been processed")
            if not result.get("simulation_ok"):
                raise BridgeError("simulation failed — will not broadcast the transaction")

            private_key = resolve_private_key(args.private_key)
            if private_key == "0xYOUR_PRIVATE_KEY_HERE":
                raise BridgeError("ETH_PRIVATE_KEY is not set — update .env or pass --private-key")
            if not (private_key.startswith("0x") and len(private_key) == 66):
                raise BridgeError("Ethereum private key must be a 0x-prefixed 32-byte hex string")

            account       = w3.eth.account.from_key(private_key)
            siblings_bytes = [bytes.fromhex(s[2:]) for s in result["siblings"]]
            call          = bridge.functions.verifyAndMint(
                lock.epoch, message, siblings_bytes, result["leaf_index"],
            )
            tx_params = {
                "from":    account.address,
                "nonce":   w3.eth.get_transaction_count(account.address),
                "chainId": w3.eth.chain_id,
            }
            try:
                tx_params["gas"] = int(call.estimate_gas({"from": account.address}) * 120 // 100)
            except Exception:
                tx_params["gas"] = 350_000
            tx_params.update(build_fee_params(w3))
            signed    = account.sign_transaction(call.build_transaction(tx_params))
            raw_tx    = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
            sent_hash = w3.eth.send_raw_transaction(raw_tx)
            receipt   = w3.eth.wait_for_transaction_receipt(sent_hash, timeout=300)

            result.update({
                "eth_sender":         account.address,
                "eth_tx_hash":        Web3.to_hex(sent_hash),
                "eth_receipt_status": int(receipt.status),
                "eth_block_number":   int(receipt.blockNumber),
                "eth_gas_used":       int(receipt.gasUsed),
                "status":             "submitted" if receipt.status == 1 else "reverted",
            })

            if receipt.status == 1:
                try:
                    events = bridge.events.MintFinalized().process_receipt(receipt)
                    result["mint_finalized"] = [
                        {
                            "message_id": to_hex32(bytes(e["args"]["messageId"])),
                            "epoch":      int(e["args"]["epochId"]),
                            "recipient":  e["args"]["recipient"],
                            "amount_raw": str(e["args"]["amount"]),
                            "amount_oct": raw_to_oct(int(e["args"]["amount"])),
                        }
                        for e in events
                    ]
                except Exception:
                    pass

        if args.json:
            print(json.dumps(json_ready(result), indent=2))
        else:
            print_human(json_ready(result))

        return 0 if result.get("status") in {"ready_to_send", "already_processed", "submitted"} else 2

    except BridgeError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
