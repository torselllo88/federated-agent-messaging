# E4 — Human Client Setup

Non-normative operational guide. The authoritative requirements are
[`testbed-architecture.md`](testbed-architecture.md) §7 and §11.2 and
[`experimental-protocol.md`](experimental-protocol.md) §41.

E4 is the one experiment that cannot be automated. It needs an actual person
using an ordinary Matrix client, because C4's completion rests on a human
being present in the three-party federated room rather than on a programmatic
stand-in (`research-scope.md` §6 C4, *Empirical support*).

---

## 1. What the client must be able to do

Any standard Matrix client will work provided it can:

- connect to a **custom homeserver URL** rather than a hard-coded one;
- trust a **private certificate authority**, either through the operating
  system trust store or its own.

Element Desktop and Element Web both satisfy this and are named in
architecture §11.2. A client that hard-codes its homeserver cannot be used.

Record the client name and version — they go into the E4 manifest, which the
frozen `human_llm_validation_manifest` schema requires.

---

## 2. Resolve the server name

The client must reach the Domain A Client-Server endpoint by the server name
in the certificate, `hs-a.test`. On the machine running the testbed, add a
hosts entry:

```text
127.0.0.1  hs-a.test
```

- **Windows**: `C:\Windows\System32\drivers\etc\hosts`, edited as Administrator
- **Linux / macOS**: `/etc/hosts`, edited as root

If the client runs on a *different* workstation, point `hs-a.test` at the
testbed host's address instead of `127.0.0.1`, and make sure TCP port 8449 is
reachable from that workstation.

Connecting to `https://localhost:8449` will **not** work: the certificate
names `hs-a.test`, and a standard client is right to refuse a name mismatch.

---

## 3. Trust the research CA

The testbed generates a private CA at bootstrap. Export it:

```bash
make e4-ca > research-ca.crt
```

Then import it manually. **Nothing in this repository modifies a system trust
store on your behalf** — that is deliberate, and it is why this step is
written out rather than scripted.

- **Windows**: double-click `research-ca.crt` → Install Certificate →
  Local Machine → Place all certificates in the following store →
  *Trusted Root Certification Authorities*.
  Or: `certutil -addstore -f Root research-ca.crt` from an elevated prompt.
- **macOS**: Keychain Access → System → File → Import Items, then set the
  certificate to *Always Trust*.
- **Linux**: copy to `/usr/local/share/ca-certificates/research-ca.crt` and
  run `sudo update-ca-certificates`.
- **Element Web in a browser**: the browser's own trust store is what matters,
  which on Windows and macOS is the system store above. Firefox keeps its own:
  Settings → Privacy & Security → Certificates → View Certificates →
  Authorities → Import.

### Removing it afterwards

The CA exists only for this controlled environment. To remove it:

- **Windows**: `certmgr.msc` → Trusted Root Certification Authorities →
  Certificates → delete the research CA entry.
  Or: `certutil -delstore Root "<CA common name>"`.
- **macOS**: delete the certificate from the System keychain.
- **Linux**: remove the file from `/usr/local/share/ca-certificates/` and run
  `sudo update-ca-certificates --fresh`.

Also remove the `hs-a.test` hosts entry.

---

## 4. Sign in

| | |
|---|---|
| Homeserver URL | `https://hs-a.test:8449` |
| User | `@actual-human:hs-a.test` |
| Password | printed by `make e4-prepare`; provisioned at bootstrap |

In Element, choose **Sign in** → **Edit** next to the homeserver → enter the
URL above → sign in with the username and password.

### Confirming you reached the intended homeserver

Open `https://hs-a.test:8449/_matrix/client/versions` in a browser on the same
machine. You should get a JSON document and **no certificate warning**. A
warning means the CA is not trusted yet; a connection failure means the hosts
entry or the port is wrong.

`make e4-prepare` checks the same things from inside the testbed and will tell
you which one is failing.

---

## 5. Running a session

```bash
make e4-prepare      # readiness, connection details; creates nothing
make e4              # ONE session; waits for you
```

`make e4` will:

1. create a fresh three-party federated room;
2. assert room version 12 and encryption disabled;
3. invite you and start the LLM-backed agent;
4. print the room details and wait.

In your client: **accept the invitation**, then send at least **three ordinary
natural-language messages** and watch for a reply to each. Anything safe and
simple works — the experiment validates the communication path, not the
model's answers, and prompts must not be chosen after seeing which ones the
model handles well (Task 06 §13).

Keep the prompts neutral. E4 is a research validation, not a personal
conversation archive; the transcript becomes evidence (§19).

When the three replies have arrived, the session asks whether you saw them,
writes the transcript and manifest, and exits. Run `make e4` three times in
total — a fresh room each time, as §41 requires.

```bash
make e4-validate     # checks all recorded sessions
```

---

## 6. Provider configuration

The agent needs an LLM credential. It is read from the environment and is
never written to telemetry, manifests or transcripts.

```bash
export FAM_LLM_PROVIDER=openai_compatible
export FAM_LLM_BASE_URL=https://openrouter.ai/api
export FAM_LLM_MODEL=<model id>
export FAM_LLM_API_KEY=<your key>
```

For Anthropic directly, use `FAM_LLM_PROVIDER=anthropic` and omit
`FAM_LLM_BASE_URL`.

Each request sends only the minimal system instruction and the text of that
one message. There is no conversational memory, no retrieval and no tool use.
