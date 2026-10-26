# Remote access

How to use TrendRelay from your phone or another computer, away from home, without installing
anything system-wide and without opening your router.

## What works with no setup

On the same Wi-Fi, open `http://<this computer's address>:3001`. The API treats loopback and private
LAN addresses (10.x, 172.16-31.x, 192.168.x) as you.

## Away from home: the built-in tunnel

Tools > Remote access, on the computer that runs TrendRelay:

1. Press **Start remote access**. This starts a Cloudflare quick tunnel (`cloudflared`, already
   installed here; no account) to the web app and shows its address, for example
   `https://quiet-river-demo.trycloudflare.com`. The address changes each time.
2. Press **New pairing code**. It is eight characters, works once, and lasts five minutes.
3. On the phone, open `<address>/remote`, enter the code, and press **Pair this device**. The phone
   keeps a device token and opens TrendRelay.
4. Press **Stop remote access** when done. Closing TrendRelay also ends the tunnel.

## How it is kept safe

- A tunnel connects to the API from this computer's own loopback address, which the API trusts. So
  `remote_gate.py` rewrites every request that carries Cloudflare's headers to come from the
  visitor's address (or a non-routable one when that is missing or private). Every existing check
  then sees a remote client: no automatic local sign-in, no loopback-only action, no desktop device
  pairing.
- Without a device token the API answers 401. The controls (status, start, stop, code) answer only
  this computer's loopback, so the tunnel cannot reach them.
- A code is single use, expires after five minutes, and is retired after five wrong guesses across
  all live codes. Pairing attempts are limited to ten a minute in total. Codes only exist while the
  tunnel runs, and stopping it clears them.
- A paired device holds a signed token that expires after `DEVICE_TOKEN_TTL_HOURS`. Rotating
  `DEVICE_TOKEN_SECRET` (or deleting `.data/device-token-secret`) signs every paired device out.
- The tunnel address is unguessable but public. Anyone who has it can load the pages; they cannot
  use the API without a token. Do not share the address and the code together.

## Optional: a private network such as Tailscale

If you already run one, `TRUSTED_CLIENT_NETWORKS=100.64.0.0/10` in `.env` makes clients inside that
network count as you, and the web app accepts pages served from tailnet addresses and `*.ts.net`
names. It is not needed for the tunnel.

## Checking it

Unit tests cover the gate, pairing, rate limits, expiry and the tunnel start and stop with a stand-in
for `cloudflared`. A run through a real tunnel has not been done: start remote access, then from the
phone check that the page loads, that the code pairs, that an edit saves, and that Library media
plays. Without a token, `<address>/api/workspaces` must answer 401.
