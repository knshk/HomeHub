# User Guide — Home LLM Hub

Welcome! The Home LLM Hub is your family's private AI on a box in the house. It
runs entirely on your home network — nothing you type, upload, or generate
leaves the house. This guide shows you how to join and use it from a phone or
laptop.

No app to install, no account to create, no password to remember.

---

## 1. Joining from a phone or laptop

1. **Get on the same Wi-Fi** as the Hub box. (If you're on mobile data or a
   different network, it won't be reachable — that's by design.)
2. **Open the Hub in your browser:**
   - `http://llm.home` — the friendly name, if your admin set it up, **or**
   - `http://llm.local` — automatic mDNS fallback (works on most phones/laptops
     with no setup), **or**
   - `http://<LAN_IP>:8090` — the box's address and port directly, e.g.
     `http://192.168.1.42:8090`. Ask your admin for the exact one.
3. **Type a username** (any name you like — it's just a label, no password) and
   tap **Join**.
4. **Wait for approval.** Your device is now "pending". The household **admin**
   has to approve it once. Ping them; once they approve, refresh the page and
   you're in.

After that, this browser stays signed in — you won't have to do it again on this
device. A different phone or browser counts as a **new device** and needs its
own one-time approval.

### Tips

- **Add to Home Screen.** On iPhone (Safari: Share -> Add to Home Screen) or
  Android (Chrome: menu -> Add to Home screen) you get an app-like icon. It's
  still the website, just one tap away.
- **"Not secure" warning?** Expected and fine on your home network. The Hub uses
  plain HTTP on the LAN; the warning is your browser noticing there's no HTTPS.
  See [`privacy.md`](privacy.md) for why that's an accepted trade-off at home.
- **Android can't find `llm.home`?** Turn off **Private DNS**
  (Settings -> Network/Connections -> Private DNS -> Off / device default), or
  just use `http://llm.local` or `http://<LAN_IP>:8090`.
- **What can I do?** Your admin assigns a role (guest / member / admin) and
  privileges. A **guest** typically gets chat only; a **member** also gets
  notes, checklists, and files/photos. If a feature is missing, ask the admin to
  grant the privilege.

---

## 2. Chat

The chat assistant is your local AI (Qwen2.5-7B running on the house box).

- Start a new conversation, type your question, and send. The reply **streams**
  in word by word.
- Conversations are **private to you** and saved, so you can scroll back or
  continue later. Give them titles to stay organized.
- Delete a conversation any time; it's gone from the box.
- It's a 7B model on local hardware: capable and private, but smaller and slower
  than a big cloud model. Great for everyday questions, drafting, summarizing,
  and brainstorming.

Privacy: your prompts and the answers never leave the house — they go to the
local gateway and back. Nobody else (not even other family members) can see your
conversations.

---

## 3. Notes

Quick sticky notes for yourself.

- Create a note with a title and body; pick a **color**; **pin** the important
  ones to the top.
- Edit or delete any time.
- Notes are **private to you**.

Good for: passwords hints (not actual passwords!), recipes, ideas, a running
"don't forget" list.

---

## 4. Checklists

Simple to-do lists with checkable items.

- Make a checklist (e.g. "Groceries", "Trip packing", "Saturday chores").
- Add items, check them off as you go, edit the text, remove items, reorder
  them.
- Checklists are **private to you**.

---

## 5. Uploading and searching files & photos

Store documents and pictures on the box and find them later by **meaning**, not
just filename.

### Uploading

- Upload **files** (PDF, Word `.docx`, plain text) or **photos** (JPG, PNG,
  etc.).
- Tick **Shared** if you want other family members to be able to find and read
  it. Leave it off to keep it private to you.
- After upload the Hub indexes the content automatically:
  - **Documents:** the text is extracted, split into chunks, and each chunk gets
    a searchable embedding.
  - **Photos:** the Hub looks at the image and writes a short **caption**
    describing the objects, scene, and any visible text, then makes that
    searchable. So you can find a photo by what's *in* it — "receipt", "whiteboard
    with the plan", "dog at the beach" — even though you never typed those words.

### Searching

- Go to **Search**, type what you're looking for in plain language, optionally
  filter by kind (file vs photo), and search.
- Results are ranked by a blend of **semantic similarity** (meaning) and
  **keyword** match, each with a short snippet showing why it matched.
- You only ever get results you're allowed to see: **your own** items plus
  anything marked **shared** by others. (You'll need the relevant read
  privilege — `files_read` / `photos_read`.)

### Deleting

- You can delete your own files and photos at any time. Only the **owner** (or
  an admin) can delete an item — a shared file is still owned by whoever uploaded
  it.

---

## 6. Your personal API key (use the house model from your own apps)

If your admin grants you the `api_keys` privilege, you can mint a personal key
and call the household model from your own scripts, apps, or tools — using the
standard **OpenAI** or **Anthropic** SDKs.

### Generate a key

1. Open **API Keys** in the Hub.
2. Give the key a name (e.g. "my laptop", "weekend project") and create it.
3. **Copy the key immediately** — it's shown to you **once** and never again.
   The Hub stores only a prefix to help you recognize it later; if you lose it,
   revoke it and make a new one.

Along with the key, the Hub shows you the **base URL** and **model** to use:

| Setting    | Value                                |
| ---------- | ------------------------------------ |
| `base_url` | `http://<LAN_IP>:8080/v1`            |
| `api_key`  | `qwsk-...` (the key you just copied) |
| `model`    | `qwen2.5-7b`                         |

> Note the **port 8080** here. That's the **gateway** (the model's front door),
> not the Hub's web port. Your minted key works directly against the gateway, so
> your own apps don't go through the Hub at all.

### Use it — OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.1.42:8080/v1",   # http://<LAN_IP>:8080/v1
    api_key="qwsk-...",                         # your key from the Hub
)

resp = client.chat.completions.create(
    model="qwen2.5-7b",
    messages=[{"role": "user", "content": "Give me a one-line dinner idea."}],
)
print(resp.choices[0].message.content)

# Streaming:
for chunk in client.chat.completions.create(
    model="qwen2.5-7b",
    messages=[{"role": "user", "content": "Count to five."}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

### Use it — Anthropic Python SDK

The gateway also speaks an Anthropic-style **Messages** shape at `/v1/messages`,
so Anthropic-SDK code works against the local model with only a base-URL change.

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://192.168.1.42:8080/v1",   # http://<LAN_IP>:8080/v1
    api_key="qwsk-...",                         # sent as x-api-key automatically
)

msg = client.messages.create(
    model="qwen2.5-7b",
    max_tokens=256,
    messages=[{"role": "user", "content": "Say hi in one word."}],
)
print(msg.content[0].text)
```

> This is a compatibility **shim**, not Claude and not Anthropic's service. It
> lets Anthropic-SDK apps target the local Qwen model unchanged. Capabilities
> match what Qwen2.5-7B supports, which is a subset of a frontier model. Don't
> present its output as coming from Claude.

### Use it — curl

```bash
curl http://192.168.1.42:8080/v1/chat/completions \
  -H "Authorization: Bearer qwsk-..." \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5-7b","messages":[{"role":"user","content":"hello"}]}'
```

### Revoke a key

If a key leaks or you're done with it, revoke it from the Hub's **API Keys**
screen. It stops working immediately. Revoking one key doesn't affect your
others.

Reminders:

- Keep your key like a password. Anyone with it can use the house model **as
  you** until you revoke it.
- The key only works **on the LAN** (it points at a `192.168.x.x` address). It's
  useless off your network — which is the point.
- Your API usage and your Hub web session are separate; signing out of the
  browser doesn't revoke your keys, and revoking a key doesn't sign you out.

---

## Troubleshooting

| Symptom                              | Fix                                                                          |
| ------------------------------------ | --------------------------------------------------------------------------- |
| Page won't load at all               | Confirm you're on the **same Wi-Fi** as the box. Try `http://<LAN_IP>:8090`. |
| Stuck on "pending / waiting"         | Ask the admin to approve your device under **Admin -> Devices**.            |
| `llm.home` doesn't resolve (Android) | Disable **Private DNS**, or use `http://llm.local` / `http://<LAN_IP>:8090`. |
| A feature/button is missing          | You may lack that privilege. Ask the admin to grant it.                     |
| Signed out / asks to join again      | You're on a new browser/device, or your device was revoked. Re-join; the admin re-approves. |
| API key call returns `401`           | Key is wrong or revoked — generate a fresh one in the Hub.                  |
| API key call returns `429`           | You hit the per-key rate limit; slow down and retry shortly.               |
