# Install HomeHub — the free edition

HomeHub is your family's own private AI, running on one small computer in your
home. Everyone in the house gets a private login, a chat assistant, sticky
**notes**, **checklists**, and **file & photo** storage you can search by typing
what you remember. Nothing leaves your home — there is no cloud account and no
subscription for the AI.

This guide is for the **free edition** (called **"Type A"** — see the bottom of
this page). It installs the HomeHub app itself; you then **connect it to an AI**
("BYO-LLM" — explained below). It takes about 10–15 minutes the first time.

> **Two computers, one idea.** You install HomeHub **once**, on a single
> always-on computer (a spare laptop or mini-PC is perfect). Everyone else in the
> house just opens it in a web browser on their phone, tablet, or laptop —
> **nothing to install on their devices.**

---

## Before you start

- **One computer to be the "hub."** Leave it on and on your home Wi-Fi. A
  machine with **16 GB of RAM** runs the local AI comfortably; less RAM still
  works if you connect a cloud AI instead (see "Connect an AI" below).
- **Your home Wi-Fi.** The hub and your phones/tablets must be on the same
  network.
- **15 minutes.**

---

## Step 1 — Install the HomeHub app (pick your system)

Download the installer for the hub computer's operating system, then follow the
one step for it.

### 🐧 Linux — one file, no installation

> ✅ This is the path we build and test directly. It is ready to run.

1. Download **`HomeLLMHub-x86_64.AppImage`**.
2. Make it runnable once (right-click → *Properties → Permissions → Allow
   executing file as program*, **or** in a terminal):
   ```bash
   chmod +x HomeLLMHub-x86_64.AppImage
   ```
3. **Double-click it.** HomeHub starts and your browser opens at the hub.

Prefer a managed install with a background service that survives reboots? Use the
`.deb` instead:
```bash
sudo apt install ./HomeLLMHub_*_amd64.deb
```

### 🍎 macOS — drag to Applications

> ⚠️ The signed macOS build requires an Apple-notarized release. Until that
> release is published, you may see a Gatekeeper warning; the steps below are the
> intended one-click flow.

1. Download **`HomeLLMHub.dmg`** and double-click it.
2. Drag **HomeHub** onto the **Applications** folder.
3. Open **HomeHub** from Applications. (First time only, if macOS warns: right-
   click the app → **Open** → **Open**.)

### 🪟 Windows — double-click the installer

> ⚠️ The signed Windows build requires a code-signing certificate. Until that
> release is published, Windows SmartScreen may warn ("More info → Run anyway");
> the steps below are the intended one-click flow.

1. Download **`HomeLLMHubSetup.exe`** and double-click it.
2. Click **Next → Install → Finish**. HomeHub launches and opens your browser.

When the app is running, the hub computer's browser shows the HomeHub welcome
screen. Now connect an AI.

---

## Step 2 — Connect an AI ("BYO-LLM")

**BYO-LLM means "Bring Your Own LLM"** — you choose the AI brain that powers
HomeHub. HomeHub is the friendly family app; the *thinking* comes from an AI you
point it at. This keeps you in control: no forced subscription, no vendor lock-in,
and (with the local option) **nothing ever leaves your house.**

You have two choices. **Most families should start with the local option.**

### Option A — Local AI (recommended, fully private, free)

Run the AI **on the hub computer itself** using **Ollama**, a free app that runs
open AI models locally. Your chats never touch the internet.

1. **Install Ollama** on the hub computer from **https://ollama.com** (one click;
   Linux/macOS/Windows all supported).
2. **Download the models HomeHub uses** (one time — this downloads a few GB):
   ```bash
   ollama pull qwen2.5:7b-instruct-q4_K_M   # the chat brain
   ollama pull nomic-embed-text             # search over your files & photos
   ollama pull moondream                    # search photos by describing them
   ```
   (These exact names are the smaller, RAM-friendly versions HomeHub expects. A
   plain `ollama pull qwen2.5` also works for the chat brain if you have the RAM.)
3. In HomeHub's welcome screen, choose **"Use local AI (Ollama)"**. HomeHub finds
   Ollama automatically. Done.

> **Why local?** It is the whole point of HomeHub: private, offline, no per-
> message fees. A computer with ~16 GB RAM handles it well. The first answer
> after the hub has been idle can take a few seconds while the model loads.

### Option B — Cloud AI (use your own provider key) — *coming soon*

Prefer a cloud AI (for example a paid provider you already use)? A later HomeHub
update lets you **paste your own provider API key** in **Settings → AI**. You pay
that provider directly at their normal rates — HomeHub never resells AI or adds a
markup; it just uses the key you provide. Until that update ships, use the local
option above.

> Whichever you pick, **your notes, files, photos, and logins always stay on your
> hub.** Only the chat text you send to a *cloud* AI would leave your home — the
> local option sends nothing out at all.

---

## Step 3 — Open HomeHub from your phone, tablet, or laptop

Everyone else in the house connects with **just a web browser** — no app to
install. There are three ways to find the hub, easiest first.

### 1. Type the friendly name

On the same Wi-Fi, open a browser and go to:

```
http://homehub.local
```

Most phones and laptops find it automatically (this uses your network's built-in
device-discovery, "mDNS"). If it doesn't load, try the next method.

### 2. "Scan for HomeHub on my network" — *in the apps, coming soon*

The upcoming HomeHub phone/desktop apps add a **"Scan for HomeHub on my
network"** button on the login screen. Tap it and it finds the hub for you, so
nobody has to type anything. If a scan finds no hub (some guest/segmented Wi-Fi
networks block this), the app simply asks you to **enter the IP address** — see
below.

### 3. Enter the hub's IP address

This always works, even when the friendly name doesn't.

- **Find the address on the hub computer.** HomeHub shows it on screen when it
  starts (something like `http://192.168.1.42:8090`). To look it up yourself:
  - **Linux:** run `hostname -I` (use the first number).
  - **macOS:** *System Settings → Wi-Fi → Details → IP Address*.
  - **Windows:** open *Command Prompt*, run `ipconfig`, read the *IPv4 Address*.
- On your phone/tablet/laptop browser, go to that address, for example:
  ```
  http://192.168.1.42:8090
  ```

**First person to connect becomes the family admin.** Pick a name, and you're in.
Everyone who joins after that appears to the admin as **"waiting for approval"** —
the admin taps **Approve** once and that device is remembered. This is how
HomeHub keeps your family's AI private to your household.

> **Android tip:** if `homehub.local` won't load, turn off *Settings → Network &
> internet → Private DNS* (set it to **Off** or **Automatic**), or just use the
> hub's IP address.

---

## Uninstall

Your notes, files, and photos live in a data folder on the hub computer; removing
the app does not delete them unless you also delete that folder.

- **Linux (AppImage):** delete the `.AppImage` file. (Data folder:
  `~/.local/share/HomeLLMHub`.)
- **Linux (.deb):** `sudo apt remove homellmhub`.
- **macOS:** drag **HomeHub** from Applications to the Trash. (Data folder:
  `~/Library/Application Support/HomeLLMHub`.)
- **Windows:** *Settings → Apps → HomeHub → Uninstall*. (Data folder:
  `%LOCALAPPDATA%\HomeLLMHub`.)

To also erase your data, delete the data folder shown above. To stop the local
AI, uninstall **Ollama** separately.

---

## Editions: free now, more later

- **Type A — the free edition (this guide).** You install HomeHub and **connect
  your own AI** (local Ollama now; your own cloud key later). Free, private, no
  subscription for the AI.
- **Type B — the bundled edition (coming next).** A future version ships with the
  local AI models **already included**, so there is no separate Ollama step — you
  install once and it just works. Same private, offline HomeHub; less setup.

---

## Quick troubleshooting

| Problem | Try this |
|--------|----------|
| Browser can't open `homehub.local` | Use the hub's **IP address** (Step 3, method 3). On Android, turn off **Private DNS**. |
| "Can't open" / SmartScreen / Gatekeeper warning | You have an **unsigned** test build. macOS: right-click → **Open**. Windows: **More info → Run anyway**. Signed releases remove this. |
| Chat says it can't reach the AI | Make sure **Ollama** is installed and running on the hub, and that you ran the `ollama pull` commands in Step 2. |
| First answer is slow | Normal after the hub has been idle — the model is loading into memory. It's fast after that. |
| A family member can't get in | The **admin** must **Approve** their device once from the admin screen. |

Want the full background (security model, how your data is handled)? See
[`docs/privacy.md`](privacy.md) and the project [`README`](../README.md).
