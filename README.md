# oct-bridge

A standalone command-line utility for bridging **OCT** from the Octra network to Ethereum as **wOCT** (Wrapped OCT).

It handles the full flow in a single command: signing and submitting a `lock_to_eth` transaction on Octra, waiting for the bridge header to appear on Ethereum, constructing the Merkle proof, and broadcasting `verifyAndMint` on the bridge contract.

---

## Requirements

- Python 3.10 or later
- An Octra wallet with OCT balance
- An Ethereum wallet with ETH for gas fees

---

## Installation

Clone this repository:

```bash
git clone https://github.com/airdropumbrella/oct-bridge.git
cd oct-bridge
```

Install dependencies:

```bash
pip install web3 requests eth-abi pynacl
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

```env
OCTRA_PRIVATE_KEY=       # base64-encoded Octra private key
BRIDGE_EVM_RECIPIENT=    # Ethereum address to receive wOCT
ETH_PRIVATE_KEY=         # 0x-prefixed Ethereum private key
```

The remaining values (RPC endpoints, contract addresses) are pre-filled and do not need to be changed.

> **Keep `.env` private. Never commit it to version control.**

---

## Usage

### Bridge OCT and claim wOCT in one step

```bash
python bot.py --amount 10 --wait-header 1800 --send
```

### Lock only — get the tx hash and claim later

```bash
python bot.py --amount 10 --lock-only
```

### Claim from an existing lock tx hash

```bash
python bot.py --tx <octra_lock_tx_hash> --send
```

### Bridge the full balance

```bash
python bot.py --all --wait-header 1800 --send
```

### Auto-retry after the daily mint cap resets (00:00 UTC)

```bash
python bot.py --tx <octra_lock_tx_hash> --auto-claim-after-reset
```

### Output as JSON

```bash
python bot.py --amount 10 --wait-header 1800 --send --json
```

---

## Parameters

| Flag | Description |
|---|---|
| `--amount <N>` | OCT amount to bridge (up to 6 decimal places) |
| `--all` | Bridge the full available balance |
| `--evm-recipient <addr>` | Override the recipient address from `.env` |
| `--tx <hash>` | Claim from an existing Octra lock tx hash |
| `--wait-header <sec>` | Max seconds to wait for the bridge header on Ethereum |
| `--send` | Broadcast `verifyAndMint` after simulation passes |
| `--lock-only` | Submit the lock and exit without claiming |
| `--auto-claim-after-reset` | Wait for UTC reset and keep retrying until the claim succeeds |
| `--poll <sec>` | Polling interval in seconds (default: 15) |
| `--json` | Print output as JSON |

---

## Example Output

```
══════════════════════════════════════════════════════════════
  OCT → wOCT  |  Octra to Ethereum Bridge
══════════════════════════════════════════════════════════════
  Octra TX               4e570c205cf5cbafe550f86dbc...
  Recipient              0x9f88119EBc98b3AD0154e99D...
  Amount                 20.000000 OCT  [20000000 raw]
  Epoch                  661077
  Source nonce           66
──────────────────────────────────────────────────────────────
  Header available       Yes
  Already processed      No
  Simulation             Passed
──────────────────────────────────────────────────────────────
  Status                 [ OK ] Submitted
  Ethereum TX            0xc22832bba5871cf32ec6055b...
══════════════════════════════════════════════════════════════
```

---

## Troubleshooting

| Error | Resolution |
|---|---|
| `ModuleNotFoundError` | Run `pip install web3 requests eth-abi pynacl` |
| `insufficient OCT balance` | Check your Octra wallet balance |
| `bridge header is not yet available` | Add `--wait-header 1800` to wait up to 30 minutes |
| `simulation failed` | Retry after a few minutes, or use `--auto-claim-after-reset` |
| `bridge contract is currently paused` | The bridge is under maintenance — try again later |
| `ETH_PRIVATE_KEY is not set` | Fill in `ETH_PRIVATE_KEY` in your `.env` file |

---

## Credits

- **Original script** — [@ylasgamers](https://t.me/ylasgamers)
- **Revised by** — [@BoresX](https://t.me/BoresX) | Channel: [@AirdropUmbrellaX](https://t.me/AirdropUmbrellaX)
