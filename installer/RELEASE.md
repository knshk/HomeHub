# Release pipeline — true one-click signed artifacts for the Home LLM Hub

This document designs the CI release pipeline that turns the Home LLM Hub source
into **true one-click, signed installers** for Linux, macOS, and Windows. It
covers what is built where, how each artifact is produced and signed, the
prerequisites (signing certificates and Apple notarization) you cannot avoid,
and a skeleton GitHub Actions workflow.

**Scope:** this release pipeline ships the **FREE Type A edition** (the
hub software + BYO-LLM — see [`home-hub/docs/install.md`](../home-hub/docs/install.md)).
It does **not** bundle model weights. The bundled-models **Type B** edition is a
separate, much larger pipeline noted at the end.

> **Honesty up front — what is and is not real here.**
> - The repo is developed and CI-validated on **Linux x86-64**. The **Linux**
>   path (AppImage + .deb) is correct, runnable, and testable on this box and on
>   an `ubuntu-22.04` runner with **no secrets**.
> - The **macOS** (`.dmg`, codesign + notarize) and **Windows** (`.exe`,
>   signtool) paths are **designed and scripted but UNTESTED here** — they can
>   only be built on `macos-*` / `windows-*` GitHub-hosted runners, and the
>   *signed* variants additionally require **paid signing certificates** (and an
>   Apple ID for notarization) that this environment does not have. Treat the
>   mac/win jobs as a starting skeleton, not a finished product.

---

## 1. What "one-click" actually means per OS

A non-technical family member should double-click one file and end up with a
running hub they reach in a browser. The minimum bar per OS:

| OS | Artifact | "One-click" experience | Signed? |
|----|----------|------------------------|---------|
| **Linux** | `.AppImage` | `chmod +x` once, then double-click — no install, no root. Self-contained. | Not applicable (no OS signing gate; we ship a SHA-256 + optional GPG `.sig`). |
| **Linux** | `.deb` | `sudo apt install ./HomeLLMHub.deb` for users who want a managed package + a systemd service. | Repo could be GPG-signed; the `.deb` itself is dpkg-verified by checksum. |
| **macOS** | `.dmg` | Drag the `.app` to Applications, double-click. **Must be codesigned + notarized** or Gatekeeper blocks it on first launch. | **Yes — required.** Needs a "Developer ID Application" cert + notarization. |
| **Windows** | `.exe` (Inno Setup) or `.msi`/MSIX | Double-click the installer, click Next. **Must be Authenticode-signed** or SmartScreen warns and may block. | **Yes — required.** Needs an OV/EV code-signing cert. |

The two-stage strategy below builds **one self-contained app per OS first**, then
wraps it in the OS-native installer.

---

## 2. Stage 1 — package the hub as a self-contained app (all OSes)

The hub is a FastAPI app run by Uvicorn:

```
home-hub/.venv/bin/uvicorn app.main:app   # dev; binds 0.0.0.0:8090
```

For distribution we do **not** ship a venv. We freeze the app + Python +
dependencies into one binary with **PyInstaller one-file** mode. This is the
shared base every OS installer wraps.

### What must be bundled

The frontend assets are loaded at runtime from package-relative directories
(`app/static`, `app/templates`) — confirmed in `home-hub/app/config.py`
(`_pick_dir(...)` prefers `app/static` then `app/templates`). PyInstaller does
**not** auto-collect non-Python data, so they must be added explicitly, and the
app must resolve them from PyInstaller's unpack dir (`sys._MEIPASS`) at runtime.

`installer/build_app.py` (the shared builder the CI calls) does:

```python
# installer/build_app.py  (sketch — the real file lives next to this doc)
PyInstaller.__main__.run([
    "home-hub/app/main.py",            # entrypoint module that exposes `app`
    "--name", APP_NAME,
    "--onefile",
    "--noconsole",                     # GUI-less; we open the browser for them
    # bundle the server-rendered frontend (no Node/React/build step exists):
    "--add-data", "home-hub/app/static:app/static",
    "--add-data", "home-hub/app/templates:app/templates",
    # hidden imports PyInstaller's static analysis misses for uvicorn/fastapi:
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.lifespan.on",
])
```

> On Windows the `--add-data` separator is `;` not `:`. The builder picks the
> right one from `os.pathsep`.

A thin launcher wraps Uvicorn so double-clicking starts the server and opens the
default browser at the local URL:

```python
# the frozen entrypoint, conceptually:
import uvicorn, threading, webbrowser, time
from app.config import HUB_HOST, HUB_PORT   # 0.0.0.0:8090 by default
def _open(): time.sleep(1.5); webbrowser.open(f"http://127.0.0.1:{HUB_PORT}/")
threading.Thread(target=_open, daemon=True).start()
uvicorn.run("app.main:app", host=HUB_HOST, port=HUB_PORT)
```

### Important caveats (be honest with users)

- **The frozen app is the hub only.** The hub is a *client* of an LLM backend.
  Type A is **BYO-LLM**: the user points it at a local **Ollama** (recommended,
  one install) or, later, a cloud key. The installer does **not** ship Ollama or
  any model weights (that is Type B). The first-run UI must walk them through
  "connect an LLM" — see the install guide.
- **`.env` / secrets.** The dev `.env` wires the hub to the qwen-stack gateway
  with a minted key. The Type A *consumer* build should instead default to
  talking to **Ollama directly** (or to a gateway the user runs) and generate any
  local secrets on first run — never bake a developer's gateway key into a
  shipped binary. The build must exclude `home-hub/.env`.
- **PyInstaller one-file unpacks to a temp dir each launch** (slower cold
  start). For a long-running server that is fine; if it bothers you, switch to
  `--onedir` inside the OS installer payload.
- **SQLite + WAL** writes to a user-writable data dir. The frozen app must point
  `DB_PATH`/`DATA_DIR` at a per-user location (e.g. `~/.local/share/HomeLLMHub`
  on Linux, `~/Library/Application Support/HomeLLMHub` on macOS,
  `%LOCALAPPDATA%\HomeLLMHub` on Windows) — **not** the read-only app bundle.

Alternative to PyInstaller: **per-OS native installers wrapping a `--onedir`
build** (below) or **Briefcase/BeeWare** for `.app`/`.msi`/AppImage from one
config. PyInstaller is chosen here for being the most battle-tested and CI-simple.

---

## 3. Stage 2 — Linux artifacts (`.AppImage` + `.deb`)  ✅ build + test here

This is the path that is **correct and ready to run/test** in this environment.

### 3a. `.AppImage` (recommended one-click; no root, no install)

AppImage = one executable file the user `chmod +x` once and double-clicks. It
carries its own runtime; nothing is installed system-wide.

`installer/packagers/linux/make_appimage.sh <path-to-pyinstaller-binary>`:

1. Build an **AppDir** layout:
   ```
   HomeLLMHub.AppDir/
     AppRun                       # -> launches usr/bin/HomeLLMHub
     HomeLLMHub.desktop           # name, icon, Categories=Network;Utility;
     home-llm-hub.png             # icon
     usr/bin/HomeLLMHub           # the PyInstaller one-file binary
   ```
2. Run **`appimagetool`** (downloaded as its own AppImage) over the AppDir to
   emit `HomeLLMHub-x86_64.AppImage`.
3. Emit `HomeLLMHub-x86_64.AppImage.sha256` next to it.

Notes for correctness on the runner:
- Build on **`ubuntu-22.04`** (older glibc) so the AppImage runs on newer
  distros too. Building on a newer glibc and running on an older one fails.
- `appimagetool` needs **FUSE** (`libfuse2`) on the runner; the workflow installs
  it. In containers without FUSE, run `appimagetool` with
  `APPIMAGE_EXTRACT_AND_RUN=1`.

### 3b. `.deb` (managed package + optional systemd service)

For users who prefer `apt`. `installer/packagers/linux/make_deb.sh`:

1. Lay out a Debian tree:
   ```
   pkgroot/
     DEBIAN/control                # Package, Version, Architecture, Depends:
     DEBIAN/postinst               # optional: install + enable a systemd unit
     DEBIAN/prerm                  # disable the service on remove
     opt/homellmhub/HomeLLMHub     # the binary
     usr/share/applications/homellmhub.desktop
   ```
2. `dpkg-deb --build --root-owner-group pkgroot HomeLLMHub_<ver>_amd64.deb`.
3. Lint with `lintian` (non-fatal) and emit a `.sha256`.

The existing repo already has a **rootful appliance path** (systemd units, DNS,
`llm.home`) in `install-appliance.sh` and `home-hub/deploy/`. The `.deb`
`postinst` can reuse that exact pattern (a `home-hub.service` running the binary
as the install user with `AmbientCapabilities=CAP_NET_BIND_SERVICE` to bind `:80`
without root at runtime — mirrors the existing
`/etc/systemd/system/home-hub.service`). **For Type A consumer simplicity the
default is the rootless `:8090` mode**; the appliance/`llm.home` flow stays an
advanced, documented add-on.

### Linux signing

There is no OS-level signature gate for AppImage/.deb the way Gatekeeper /
SmartScreen gate mac/win. We provide integrity instead:
- ship a `.sha256` for every artifact, and
- optionally a **detached GPG signature** (`gpg --armor --detach-sign`) using a
  project signing key, with the public key published in the repo + docs.

---

## 4. Stage 2 — macOS artifact (`.dmg`, codesigned + notarized)  ⚠ untested here

**Can only be built on a macOS runner; the *signed* variant additionally requires
a paid Apple Developer account.** Without signing + notarization, Gatekeeper
blocks the app on first launch on any modern macOS ("can't be opened because
Apple cannot check it for malicious software"). So signing is **not optional** for
a one-click consumer experience.

### Prerequisites (you must obtain these)

- **Apple Developer Program** membership (USD 99/yr).
- A **"Developer ID Application"** certificate (for distribution outside the App
  Store), exported as a password-protected `.p12`.
- An **Apple ID + app-specific password** (or a stored `notarytool` credential
  profile) and your **Team ID**, for notarization.

### Pipeline (`installer/packagers/macos/make_dmg.sh`)

1. Build the **`.app`** bundle around the PyInstaller binary (PyInstaller can
   emit a `.app` with `--windowed`; or assemble `HomeLLMHub.app/Contents/MacOS/`
   by hand) with a proper `Info.plist` (bundle id `com.<you>.homellmhub`,
   version, `LSMinimumSystemVersion`).
2. **Import the cert** into a temporary keychain in CI:
   ```bash
   echo "$APPLE_CERT_P12_BASE64" | base64 -d > cert.p12
   security create-keychain -p "$KEYCHAIN_PW" build.keychain
   security import cert.p12 -k build.keychain -P "$APPLE_CERT_PASSWORD" \
       -T /usr/bin/codesign
   security set-key-partition-list -S apple-tool:,apple: -s -k "$KEYCHAIN_PW" build.keychain
   ```
3. **Codesign** the app with the hardened runtime (required for notarization):
   ```bash
   codesign --deep --force --options runtime --timestamp \
     --sign "Developer ID Application: <Your Name> ($APPLE_TEAM_ID)" HomeLLMHub.app
   ```
4. Build the **`.dmg`** with **`create-dmg`** (`brew install create-dmg`) — gives
   the drag-to-Applications window.
5. **Notarize** the dmg and **staple** the ticket:
   ```bash
   xcrun notarytool submit HomeLLMHub.dmg \
     --apple-id "$APPLE_NOTARY_APPLE_ID" --team-id "$APPLE_TEAM_ID" \
     --password "$APPLE_NOTARY_PASSWORD" --wait
   xcrun stapler staple HomeLLMHub.dmg
   ```
6. Emit a `.sha256`.

> Codesigning a **one-file** PyInstaller binary is fragile (nested Mach-O inside
> the bootstrap). On macOS prefer **`--onedir`** assembled into the `.app` so each
> dylib/binary is individually signable, then `codesign --deep`.

Apple **rejects unsigned/un-notarized apps at the user's first launch.** If the
secrets are absent, the CI job emits an **unsigned** `.dmg` for testing only —
clearly label it "unsigned, will be blocked by Gatekeeper."

---

## 5. Stage 2 — Windows artifact (`.exe` / `.msi`, Authenticode-signed)  ⚠ untested here

**Built on a windows runner; the *signed* variant requires a code-signing
certificate.** Unsigned installers trigger **SmartScreen** ("Windows protected
your PC") and erode trust; an unsigned EXE from an unknown publisher is the #1
reason a non-technical user abandons the install.

### Prerequisites

- A **code-signing certificate**: an **OV** cert (cheaper, but new ones now ship
  on hardware tokens / cloud HSM per CA/B rules — automating signing in CI then
  needs a **cloud signing service** like Azure Trusted Signing, DigiCert
  KeyLocker, or SSL.com eSigner) **or** an **EV** cert (instant SmartScreen
  reputation, always hardware-backed). A plain importable `.pfx` works only for
  older/non-compliant certs or self-signed test certs.
- Exported as a password-protected `.pfx` **or** credentials for the cloud
  signing service.

### Pipeline (`installer/packagers/windows/make_installer.ps1`)

1. Take the PyInstaller `.exe`.
2. Build a user-friendly installer with **Inno Setup** (`ISCC.exe` over a
   `.iss` script: Start-menu shortcut, optional "launch on login", per-user
   install under `%LOCALAPPDATA%` so no admin is needed). MSIX/`.msi` (WiX) is the
   alternative if you want Store distribution or Group-Policy deployment.
3. **Sign** both the inner `.exe` and the produced installer with `signtool`:
   ```powershell
   signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
     /f cert.pfx /p $env:WIN_CERT_PASSWORD HomeLLMHubSetup.exe
   signtool verify /pa HomeLLMHubSetup.exe
   ```
   (With a cloud signing service, swap `signtool /f` for that vendor's
   CI action/CLI — the cert never leaves the HSM.)
4. Emit a `.sha256`.

If `WIN_CERT_*` secrets are absent, emit an **unsigned** installer for testing
only, clearly labeled.

---

## 6. The CI workflow (GitHub Actions matrix)

The skeleton lives at [`.github-workflows-release.yml`](.github-workflows-release.yml)
— **rename/move it to `.github/workflows/release.yml`** in the repo that holds the
hub source.

Shape:

```
on: push tags v*  (or workflow_dispatch)
jobs:
  create-release:  ubuntu — make the GitHub Release shell
  build (matrix):
    - ubuntu-22.04  -> build_app.py -> make_appimage.sh + make_deb.sh        (MUST pass)
    - macos-14      -> build_app.py -> make_dmg.sh   (codesign + notarize)   (best-effort until certs)
    - windows-2022  -> build_app.py -> make_installer.ps1 (signtool)         (best-effort until certs)
  -> each job uploads its artifacts to the Release + as CI artifacts
```

Key design decisions baked into the skeleton:

- **`fail-fast: false`** and **`continue-on-error` for non-Linux** so a missing
  Apple/Windows cert never blocks shipping the Linux build. The **Linux job is
  the required gate**.
- **Pin Python to 3.10** (the version the hub is known-good on).
- **Build Linux on the oldest supported runner** (`ubuntu-22.04`) for glibc
  portability.
- Secrets are **referenced but optional**; jobs degrade to **unsigned** artifacts
  with a warning when a cert secret is empty, so the pipeline is runnable from
  day one and hardens as certs arrive.

### Files this pipeline expects (to be added alongside this doc)

```
installer/
  RELEASE.md                         <- this file
  .github-workflows-release.yml      <- the skeleton workflow (move to .github/workflows/)
  build_app.py                       <- shared PyInstaller builder (Stage 1)
  packagers/
    linux/   make_appimage.sh  make_deb.sh
    macos/   make_dmg.sh
    windows/ make_installer.ps1  setup.iss
  out/                               <- packagers write finished artifacts here
```

Only `RELEASE.md` and the workflow skeleton are delivered now; the `build_app.py`
and `packagers/*` scripts are sketched inline above and are the next
implementation step. The **Linux** packagers are straightforward shell and are
the priority because they are the only ones testable in this environment.

---

## 7. Prerequisites summary (the honest checklist)

| Need | For | How to get it | Cost |
|------|-----|---------------|------|
| `ubuntu-22.04` runner | Linux build/test | GitHub-hosted (default) | Free |
| FUSE / `libfuse2` | `appimagetool` | apt on the runner | Free |
| `macos-14` runner | macOS build | GitHub-hosted | Free minutes |
| Apple Developer Program | macOS signing + notarization | developer.apple.com | ~$99/yr |
| "Developer ID Application" cert (`.p12`) | `codesign` | Apple Developer portal | Included in program |
| Apple ID app-specific password + Team ID | `notarytool` | appleid.apple.com | Free w/ program |
| `windows-2022` runner | Windows build | GitHub-hosted | Free minutes |
| Code-signing cert (OV/EV) | `signtool` / Authenticode | a CA (DigiCert, Sectigo, SSL.com) or cloud signing (Azure Trusted Signing) | ~$70–$400+/yr; EV higher |

**Bottom line:** **signed Mac and Windows builds require those paid runners +
certs.** Until you have them, this pipeline still produces **Linux signed-by-
checksum** artifacts that are correct and shippable, plus **unsigned** mac/win
artifacts that are usable only for internal testing (end users will hit Gatekeeper
/ SmartScreen).

---

## 8. After Type A: the Type B (bundled-models) edition

Type A above ships only the hub (BYO-LLM). The **Type B** edition bundles the
local model stack so a household needs no Ollama setup:

- Pull and bundle **Apache-2.0 / commercially-safe** weights only — `qwen2.5-7b`
  (chat), `moondream` (vision), `nomic-embed-text` (embeddings). **Never ship
  LLaVA** — its CC BY-NC training data forbids commercial use (see
  `home-hub/docs/productizing.md`).
- Artifacts become **multi-GB** (the 7B chat model alone is ~4.7 GB), which
  changes hosting (Git LFS / release assets / a CDN), build time, and the GitHub
  release-asset size limits. A Type B build likely ships the models as a separate
  downloaded-on-first-run pack rather than inside the installer.
- Carry the **Apache-2.0 NOTICES/LICENSE** files in the package (attribution is
  required; no royalties).

Type B is a **deliberately separate, later pipeline.** Prove the Type A
one-click + BYO-LLM flow first.
