# Firewall (ufw) - LAN-only access for the qwen-stack host

This host runs two listeners:

| Port  | Service              | Auth?                         | Who may reach it           |
|-------|----------------------|-------------------------------|----------------------------|
| 8080  | qwen-stack gateway   | Yes - API keys (`qwsk-...`)   | LAN `/24` only             |
| 11434 | Ollama               | **No auth at all**            | LAN `/24` only (ideally localhost) |

> **Why this matters.** Ollama exposes model management and inference with
> **no authentication whatsoever**. Internet scans in early 2025 found
> 175,000+ exposed instances, and CVE-2026-7482 ("Bleeding Llama", CVSS 9.3)
> lets an unauthenticated attacker read process memory (prompts, tokens, keys).
> Port **11434 must never be reachable from the WAN.** Applications should talk
> to the authenticated gateway on **:8080**, never to Ollama on **:11434**.
>
> Stricter option: bind Ollama to `127.0.0.1:11434` (see
> `deploy/ollama-subnet.conf`) so only the local gateway can reach it, and skip
> the 11434 ufw rule entirely. Also update Ollama to **v0.17.1+** to patch
> CVE-2026-7482.

---

## 1. Find your LAN subnet

```bash
ip -4 addr show scope global
# Look for e.g. "inet 192.168.1.9/24" -> subnet is 192.168.1.0/24
```

Replace `192.168.1.0/24` below with **your** subnet if different.

## 2. Apply the rules

Rule ordering matters: specific ALLOW rules must come **before** the broad
DENY, otherwise the DENY wins. ufw evaluates rules top-to-bottom.

```bash
# Enable ufw with a safe default (deny inbound, allow outbound).
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Keep your SSH session alive (adjust port if not 22).
sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp

# Gateway: allow ONLY from the LAN /24.
sudo ufw allow from 192.168.1.0/24 to any port 8080 proto tcp

# Ollama: allow ONLY from the LAN /24.
# (Omit this entirely if Ollama is bound to 127.0.0.1 - preferred.)
sudo ufw allow from 192.168.1.0/24 to any port 11434 proto tcp

# Explicitly deny both ports from everywhere else (belt-and-suspenders;
# the "default deny incoming" already blocks WAN, but this makes intent clear).
sudo ufw deny 8080/tcp
sudo ufw deny 11434/tcp

# Turn it on.
sudo ufw enable
```

> Note: with `default deny incoming` already set, the trailing `deny` rules are
> redundant but harmless - they document intent and survive a later change of
> the default policy. The per-subnet `allow` rules are inserted ahead of them
> because they are more specific, so LAN access still works.

## 3. Verify the ruleset

```bash
sudo ufw status verbose
sudo ufw status numbered
```

Expected (subnet will reflect yours):

```
To                         Action      From
--                         ------      ----
22/tcp                     ALLOW       192.168.1.0/24
8080/tcp                   ALLOW       192.168.1.0/24
11434/tcp                  ALLOW       192.168.1.0/24
8080/tcp                   DENY        Anywhere
11434/tcp                  DENY        Anywhere
```

## 4. Verify behavior

**From a LAN machine** (should connect; gateway then requires an API key):

```bash
# Gateway reachable, but unauthenticated -> 401 (this is correct/expected):
curl -s -o /dev/null -w '%{http_code}\n' http://<HOST_LAN_IP>:8080/v1/models
# 401

# With a real key it should return the model list:
curl -s http://<HOST_LAN_IP>:8080/v1/models \
     -H "Authorization: Bearer qwsk-...."
```

**From outside the LAN / WAN** (should time out or be refused):

```bash
curl -m 5 http://<HOST_PUBLIC_IP>:8080/   # expect timeout / connection refused
curl -m 5 http://<HOST_PUBLIC_IP>:11434/  # expect timeout / connection refused
nc -vz -w5 <HOST_PUBLIC_IP> 11434         # expect "timed out" / refused
```

If any WAN probe connects, **stop** and recheck: confirm `default deny
incoming` is active, the ALLOW rules are scoped to the LAN subnet (not
`Anywhere`), and no upstream router/NAT is port-forwarding 8080 or 11434.

## 5. Remote access (do NOT port-forward)

For access from outside the LAN, use a VPN (Tailscale / WireGuard) or an SSH
tunnel - never a public port-forward to 11434:

```bash
# SSH tunnel: reach the gateway on your laptop's localhost:8080
ssh -N -L 8080:127.0.0.1:8080 kanishka@<HOST_LAN_IP>
# then point your client at http://127.0.0.1:8080/v1
```

## Removing / editing rules

```bash
sudo ufw status numbered      # find the rule number
sudo ufw delete <number>      # delete by number (re-run status after each)
```
