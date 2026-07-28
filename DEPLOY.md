# Putting the converter online

The app serves two roles from one module. Locally it binds to `127.0.0.1` and
opens a browser; when a `PORT` environment variable is present — which every
hosting platform sets — it binds publicly, skips the browser, and applies the
tighter limits described below. Nothing needs to be edited to deploy.

**Live instance:** https://crossprint.onrender.com

## Deploying to Render (free)

1. Push this folder to a GitHub repository.
2. On [render.com](https://render.com): **New → Blueprint**, pick that repo.
   `render.yaml` supplies the runtime, start command, health check and limits.
3. First build takes a few minutes (it installs Python deps and uploads the
   ~15 MB preset library).

The free instance sleeps after ~15 minutes idle and takes roughly 50 seconds to
wake, so the first visit after a quiet period is slow. Everything after that is
immediate. `/healthz` is the endpoint to point an uptime monitor at if you want
to keep it warm.

Any platform that reads a `Procfile` — Railway, Fly.io, Heroku — works from the
same files without changes.

### Deploying from the public repo URL instead

Render also accepts a public repository URL directly, which avoids granting it
access to your GitHub account. That is how the live instance above was set up.
The trade-off is that **auto-deploy is off**: after pushing a change, open the
Blueprint page and hit *Manual sync*, or connect the GitHub account to have
Render redeploy on every push.

## Why the limits are what they are

A `.3mf` is a zip, and conversion holds the entire uncompressed project in
memory. Measured on the largest real sample: a 20 MB upload expands to 131 MB
of geometry and peaks at **197 MB RSS**, about 1.5× the uncompressed size, plus
~25 MB for the interpreter and preset library.

On a 512 MB instance that gives:

| Setting | Hosted default | Local |
|---|---|---|
| `MAX_UPLOAD_MB` | 80 | 300 |
| `MAX_UNCOMPRESSED_MB` | 220 | 1024 |

220 MB uncompressed predicts a ~355 MB peak, leaving real headroom. The
uncompressed figure is read from the zip directory *before* any data is loaded,
so an oversized project is refused with a clear message instead of taking the
container down.

Conversions are also serialised — one at a time, regardless of how many people
click at once — because the work is memory-bound, not IO-bound. A second
concurrent conversion would double peak usage for no throughput gain. Page
loads and health checks still respond during a conversion; a request that waits
more than two minutes for its turn gets a "busy, try again" response.

Raise both limits together if you move to a larger instance. Nothing else needs
changing.

## What visitors' files do

Uploads are held in memory for the duration of one request and are never
written to disk. Nothing is logged beyond the ordinary web-server access line,
and nothing persists after the response is sent. Running the tool locally keeps
files off any server entirely, which is the better answer for anything
confidential — the error messages for oversized projects say so.

## Optional: a tip jar

Set `DONATE_URL` to any link (Ko-fi, Buy Me a Coffee, GitHub Sponsors, PayPal)
and a modest support button appears in the footer. Leave it unset and there is
no button at all — nothing is hardcoded, so anyone self-hosting this doesn't
inherit someone else's donation link.

```
DONATE_URL = https://ko-fi.com/yourname
```

## Licensing (read before hosting)

The vendored preset libraries and the extracted config vocabularies come from
BambuStudio and Snapmaker Orca, **both AGPL-3.0**. AGPL's copyleft covers
network use: hosting this publicly obliges you to offer the source to its
users. Publishing the repository you deploy from satisfies that, which the
Render flow above does anyway. See [NOTICE.md](NOTICE.md) for the details and
for the `LICENSE` file to add before going public.

## Before sharing the link widely

- The converter has no authentication or rate limiting. That is fine for a link
  shared with a community; it is not a public API.
- Only Snapmaker U1, Bambu H2C, H2D and A1 mini are verified against real
  project files. The other eleven models warn in the UI that they're
  unverified — worth keeping that visible rather than trimming it.
