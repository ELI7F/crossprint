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

Conversion does **not** hold the project in memory. `core/archive.py` reads
parts lazily and streams anything it doesn't modify straight from the source
container into the output, a megabyte at a time, so peak memory is set by the
largest part actually rewritten rather than by the size of the project.

Measured on the largest real sample -- an 11-plate, 122 MB upload that expands
to **726 MB** of geometry across 32 object meshes:

| | Before streaming | Now |
|---|---|---|
| Peak RSS | >1 GB (never completed on a 512 MB box) | **74 MB** |
| Time | -- | 12 s |

A 66 MB project peaks at 70 MB and the 726 MB one at 74 MB: the cost is
essentially flat, because only `3D/3dmodel.model` and the ~1 MB of config parts
are ever rewritten. The 32 object meshes pass through byte-for-byte identical,
which `tests/test_streaming.py` asserts along with the memory ceiling itself.

| Setting | Hosted default | Local |
|---|---|---|
| `MAX_UPLOAD_MB` | 300 | 2048 |
| `MAX_UNCOMPRESSED_MB` | 3072 | 16384 |

These are now guards rather than predictions -- against a zip bomb, and against
an upload so large it would sit in the request for minutes. The uncompressed
figure is read from the zip directory *before* any data is loaded, so an
oversized project is refused with a clear message instead of taking the
container down. The worker timeout is 600 s to cover a slow upload of a
large project, not because conversion itself is slow.

Conversions are still serialised -- one at a time, regardless of how many people
click at once. Page loads and health checks respond during a conversion; a
request that waits more than two minutes for its turn gets a "busy, try again"
response.

### Two traps when changing the limits

**The `envVars` in `render.yaml` override `web/app.py`'s defaults.** Changing the
code defaults alone does nothing in production -- the streaming work above
shipped and the live server still refused anything over the old 80 MB, because
the blueprint was still pinning it. Change both, or neither.

**A Blueprint deployed from a public repo URL does not reliably pick up new
commits, and this cost real time.** Render's cached clone stays behind; *Manual
sync* on the Blueprint page often completes without creating a sync at all, and
the service's own *Manual Deploy -> Deploy latest commit* worked only
sometimes. Over two days the live site served a build three commits old while
every local test passed, and the user kept converting files against it and
reporting the results as broken. The build log said so plainly the whole time:

    ==> It looks like we don't have access to your repo, but we'll try to
        clone it anyway.

**The fix is to connect the GitHub account** (service Settings -> Build ->
Source -> Edit -> Connect Git provider -> GitHub), which sets Auto-Deploy to
*On Commit*. Every push then deploys on its own and the manual step disappears.
Do this before anything else on a new instance; deploying from a public URL is
only worth it to avoid granting repository access, and the cost is a deploy
path that silently does nothing.

**Check the deployed commit, not the dashboard's word for it.** The service
header shows the live commit hash. Better still, probe the running site for a
behaviour only the new build has -- see the version probes in this section's
history. A health check returning 200 says nothing about which build answered.

Verified end to end after both were fixed: a 122 MB / 726 MB-uncompressed
11-plate project uploaded to the live instance returned HTTP 200 with a valid
H2C project -- 32 meshes, 10 filaments, 11 plates re-placed -- in 166 s
including both transfers.

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
