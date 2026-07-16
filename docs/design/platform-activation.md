# Design: Platform Activation & Egress Lock

Status: **built & verified July 2026 — awaiting one sudo run.**
Code: `installer/enable-platform.sh`, `installer/egress.sh`, `installer/verify-platform.sh`, `installer/home-hub-https.service`, `installer/homehub-mdns.service`, `installer/gen-local-cert.sh`.

## Purpose

Finish the three platform items that need root exactly once:

1. **HTTPS :443** (`home-hub-https.service`) — same hub code over TLS; unlocks full Android/desktop PWA install.
2. **mDNS** (`homehub-mdns.service`) — `homehub.local` alias that survives reboots.
3. **Egress firewall allowlist** (`egress.sh lock`) — belt‑and‑suspenders LAN‑only lock on top of the app‑level offline hardening (which is already verified 0 phone‑home).

Everything is activated by **one command**: `sudo installer/enable-platform.sh` (the egress lock is a separate, optional `sudo installer/egress.sh lock` because it is reversible policy, not installation). Verification never needs sudo: `installer/verify-platform.sh`.

## Design

### Why systemd `IPAddressDeny`/`IPAddressAllow`, not nftables/iptables

Every appliance service runs as uid `kanishka` — the **same uid as the user's own desktop session** — so a uid‑match firewall rule cannot tell "ollama phoning home" apart from "the user browsing the web". systemd's `IPAddress*` directives instead attach an **eBPF filter to each service's own cgroup** (supported since systemd 235; this host runs 249), which gives:

- **exact scope** — locks precisely that process tree, nothing else; no global firewall state;
- **per‑service removability** — each lock is a plain drop‑in file, deleted independently;
- **unprivileged observability** — `systemctl show -p IPAddressDeny <svc>` works without sudo.

Allowlist: `localhost 192.168.0.0/16` (loopback + RFC1918 home LAN). Note this blocks **ingress and egress** outside those ranges — fine, since all clients are LAN devices.

### What gets locked, what deliberately doesn't

| Service | Locked? | Why |
|---|---|---|
| `ollama.service` | ✅ | model runtime must never reach the internet |
| `voice-svc.service` | ✅ | STT/TTS is local‑only |
| `home-hub.service` | ✅ | hub talks only to LAN + local services |
| `home-hub-https.service` | ✅ once installed | **same hub code** — must not become the one unlocked route out |
| `qwen-gateway.service` | ❌ **never** | the disclosed, **sanctioned egress path** for cloud providers (see `cloud-providers.md`); `verify-platform.sh` actively FAILs if a lock drop‑in ever appears on it |

Drop‑ins are written to `/etc/systemd/system/<svc>.d/90-egress-lock.conf` — named `90-…` so they sort after any other drop‑in.

### Unlock is a documented temporary state

Ollama registry pulls and hub HuggingFace image‑model downloads live outside the LAN, so they need a temporary unlock:

```
sudo installer/egress.sh unlock    # prints a loud re-lock reminder
# ... pull the model ...
sudo installer/egress.sh lock
```

`egress.sh status` (no sudo) shows per‑service state and distinguishes **"LOCKED (loaded)"** from **"locked on disk, not loaded yet"** (drop‑in present but the service hasn't been restarted) via `systemctl show`.

### Idempotency & least disruption (`enable-platform.sh`)

- Unit files are `cmp`‑compared before copying — re‑runs cause **zero** daemon‑reloads/restarts when nothing changed.
- Certs are regenerated **only** if missing or within 30 days of expiry, and generation runs as the **repo owner via `runuser`** (root‑owned keys would be unreadable by the `User=kanishka` service).
- Lock idempotency in `egress.sh` compares byte‑exact with `printf '%s' | cmp -s - file` — the earlier `[ "$(cat file)" = "$CONTENT" ]` form never matched because `$()` strips the trailing newline (bug found by simulation, fixed).

### Verification semantics (`verify-platform.sh`)

- **SKIP** = feature simply not activated yet (unit not installed); **FAIL** = installed but broken. Exit 0 iff nothing FAILed.
- Unit syntax check trusts `systemd-analyze verify`'s **exit code** but filters its output to our unit — it chats about unrelated units (e.g. snapd `RestartMode` warnings on this host).
- Checks: unit syntax, cert presence/expiry (warn <30 days), mDNS resolution, HTTPS :443 reachability (TLS‑verified against our own CA when resolvable), egress drop‑ins on all locked services, and the gateway staying unlocked.

## API surface

None — shell only. Three entry points:

| Command | Sudo | What |
|---|---|---|
| `sudo installer/enable-platform.sh` | yes | certs (if needed) + install/enable/start HTTPS + mDNS units |
| `sudo installer/egress.sh lock \| unlock` | yes | add/remove the LAN‑only eBPF lock per service |
| `installer/verify-platform.sh` / `installer/egress.sh status` | **no** | health check / lock state |

## Security

- The egress lock is **defence in depth**, not the primary control — app‑level offline hardening (telemetry off, model libs offline) remains first line and is independently verified.
- The lock covers ingress too: locked services are unreachable from outside RFC1918 + loopback even if a port were exposed.
- The single sanctioned egress path (`qwen-gateway`) is the one place cloud traffic can originate, which is exactly what the privacy dashboard should disclose.
- Certs: local CA at `home-hub/certs/rootCA.crt` (served for one‑time trust at `/static/homehub-ca.crt`), leaf key readable only by the service user.

## Tests

Shell scripts are exercised by **simulation and re‑run** rather than pytest: lock → status → unlock → status cycles, double‑runs of `enable-platform.sh` (asserting zero restarts on the second run), and `verify-platform.sh` in both pre‑ and post‑activation states. The idempotency bug above was caught this way.

## Operational notes

- After activation: trust the CA once per device (`http://homehub.local/static/homehub-ca.crt`), then `https://homehub.local/` installs the PWA fully.
- `homehub-mdns.service` needs `avahi-daemon` (`sudo apt install avahi-daemon` if missing — the script warns).
- If a locked service can suddenly reach the internet, check `egress.sh status` first: a drop‑in removed without re‑lock, or a unit renamed, are the likely causes.
- Never lock `qwen-gateway.service`. If cloud providers are unwanted, disable them at the gateway (see `cloud-providers.md`) instead of firewalling the sanctioned path.
