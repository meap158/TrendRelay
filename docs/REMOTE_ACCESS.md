# Remote access

How to use TrendRelay from your phone or another computer, at home or away, without opening it to the
internet and without a public hostname.

## What works with no setup

On the same Wi-Fi, open `http://<this computer's address>:3001`. The page finds the API on the same
address at port 8011, and the API treats loopback and private LAN addresses (10.x, 172.16-31.x,
192.168.x) as you. Nothing below changes that.

## Away from home: Tailscale

Tailscale is a private network between your own devices. The computer and the phone both join it, and
only they can reach each other. TrendRelay then treats the tailnet like your LAN.

1. Install Tailscale on this computer and on the phone, and sign in to the same account on both.
2. In the project's `.env`, add `TRUSTED_CLIENT_NETWORKS=100.64.0.0/10` and restart the API
   (`npm run dev` or the launcher). Leave it out to keep today's behaviour exactly.
3. On the phone, open `http://<computer's tailnet name or 100.x address>:3001`. The tailnet name is
   shown in the Tailscale app, for example `my-pc.tail1234.ts.net`.

What the setting does: clients whose address is inside the network you list are trusted as you,
and pages served from tailnet addresses and `*.ts.net` names may read the API. What it does not do:
it does not open a port on your router, it does not make anything public, and a client outside the
list still needs a bearer token like any other. Machine-level actions keep their own loopback-only
guards.

## Why not a public https hostname

The API trusts you by address. A tunnel to a public hostname forwards from this machine, so
without a login in front (Cloudflare Access) every visitor would arrive as a trusted client. The
web server's `/api/*` rewrite and the https branch of `apiBaseUrl` exist for that case and stay,
but nothing here depends on them. uvicorn takes the client address from `X-Forwarded-For` only when
the request comes from 127.0.0.1, so a tunnelled request is seen as the visitor's own address.
That behaviour is a default of the installed uvicorn and is not tested here. Do not point a public
hostname at port 3001 without an access layer in front of it.

## Checking it

Not yet verified on a real tailnet, because Tailscale is not installed on this computer. After
setup, check from the phone: the page loads with its styles, the workspace stops saying
"Loading workspace", a write works (edit a draft title), and an image or video from the Library
plays. If the page loads but the API calls fail, see the browser console for a CORS or 401 error.
A request from an address outside `TRUSTED_CLIENT_NETWORKS` is refused with 401.
