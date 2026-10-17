# Bounded Windows public hosting

Status: OWNER-OPERATED PUBLIC DEMO. The owner approved initial anonymous
exposure on 2026-10-18. Account isolation and private token setup passed. Public
TLS, WSS, AI, Manual Search, PDF bytes and Replay passed protocol checks through
ngrok. A real phone on mobile data confirmed anonymous Overview, the PHC-S
45.95% / N=74 AI result, opening the PDF, the Urology/CIP/urine/age-65+
Insufficient Data result at N=18, and scenario step 3 at historical 10% / current
40% in Overview Recent Changes. The public browser bootstrap now
passes the same 58-module HTTP/2 burst that reproduced the original blank page.
The hostname survived an agent restart. Consult the owner verification report
for the exact test scope and current live/offline state; these checks do not
guarantee continuous availability. Maintenance stops the foreground endpoint.
The Phase 15 local launcher remains available unchanged.

## Provider

Selected account: ngrok Free. Assigned development origin:
`https://secular-bundle-uncapped.ngrok-free.dev`.
The account dashboard confirms that this is an assigned development domain,
not a purchased custom domain. Do not use a random Quick Tunnel for submission.

The [official Free limits](https://ngrok.com/docs/pricing-limits/free-plan-limits)
list 1 GB outbound/month, 20,000 HTTP requests/month, 4,000 HTTP requests/minute,
three online endpoints, and a browser interstitial. Its Visit button suppresses
the interstitial for seven days using a cookie. A private mobile browser showed
the ordinary Visit Site page and then loaded the demo without a login. Do not
bypass it with custom headers when verifying the normal visitor experience.
[WebSockets are supported](https://ngrok.com/docs/using-ngrok-with/websockets).
TLS and same-domain agent restart passed live checks. Free quotas still apply;
the remaining monthly quota and credit must be monitored by the owner.

The signed-in dashboard on 2026-10-17 showed Free, 1 GB outbound/month, three
online endpoints, 4K HTTP requests/minute, "Unlimited" endpoint connections and
15 Traffic Policy rules. The public documentation lists different policy and
connection caps. Use the lower documented limits when planning capacity; this
discrepancy has not been resolved. A displayed policy limit is not proof that a
specific rate-limit action is included without charges. No paid feature or
policy has been activated. Edge rate limiting is not relied on by this build.
Do not add billing or change plans without owner approval.

Official portable agent tested: 3.39.11, valid Authenticode signature ngrok, Inc.
The first agent endpoint authenticated under AEIDemo. Credentials remain outside the project
and Git. The owner must enter the token privately; never paste it into chat.

## Local kernel and admission check

From the project directory, using the existing installed environment:

```powershell
& 'C:\Users\Denis\PycharmProjects\JunctionX\.venv\Scripts\python.exe' -B -m src.windows_job --local-check
```

This is a localhost test under the developer account. It does NOT provide
personal-file isolation and must not be connected to a public tunnel.
The supervisor creates a suspended worker, assigns kernel limits, and only then
resumes it. Assignment failure aborts startup. Ctrl+C closes only this owned job.
No service, scheduled task, automatic restart or firewall rule is installed.

Limits:

| Resource | Bound |
| --- | --- |
| Worker committed memory | 2 GiB, Windows Job Object |
| Worker CPU | 25% aggregate machine CPU, kernel hard cap |
| Worker processes | 1; child process creation blocked |
| Active WebSockets | 4, nonwaiting OS file locks |
| Connection lifetime | 15 minutes, then fresh session |
| Slot reuse | 45-second cooldown after disconnect; crash reservation up to 15m45s |
| New WebSockets | 12/minute, 60/hour globally |
| HTTP requests | 240/minute, 3,600/hour globally |
| WebSocket frames | 30/minute, 300/connection; global 120/minute, 900/hour |
| Frame/widget state | 1 MiB |
| Media downloads | 20/minute, 120/hour globally |
| HTTP concurrency / transport backlog | 8 / 64 |
| Static requests waiting for an HTTP slot | At most 56, up to 5 seconds; excess gets 503 |
| Transport connections / receive queue | 64 (includes idle keepalive and WS) / 4 frames |
| HTTP application lifetime / idle keepalive | 30 seconds / 1 second |
| Headers / URL plus query | 16 KiB / 2 KiB |
| AI | Existing 1 slot, 6/minute and 30/hour, fixed 8B identity |
| Analysis | Existing 2 slots, 90/minute and 900/hour |

Persistent global minute buckets conservatively include the previous whole
minute and the preceding 60 whole minutes. Limits may reject earlier than an
exact sliding window; a restart does not reset them. Corrupt state and clock
rollback fail closed. Journals are fixed-size and contain counts and timestamps,
not addresses, questions, headers or session identifiers. Do not delete budget
storage to bypass controls during a public run.

The HTTP application cap remains eight active handlers. Only `/static/` assets
may wait briefly for a slot; downloads, health and analysis do not queue. A
normal cold page requests 58 initial JS modules plus later component assets.
Immediate rejection at eight handlers, combined with a transport cap counting
idle proxy connections and a 16-entry TCP listen backlog, caused blank pages
through ngrok. A real HTTP/2 single-page burst reproduced ERR_NGROK_8012 while
separate HTTP/1.1 requests passed. The listen backlog is bounded at 64 and the
static wait admits up to 56 pending assets for at most five seconds, retaining
eight active handlers and 64 total transport connections. These limits fit a
single cold page's 58 module preloads without creating an unbounded queue.
The bounded wait and shorter keepalive address this without increasing any global rate budget,
WebSocket budget or kernel resource cap. Cold loads and repeated reloads consume
the same 240/minute global HTTP budget; simultaneous cold starts may need to be
staggered. Do not reset production budgets to make a test pass.

Forwarded identity/IP headers are removed. Exact Host and Origin allowlists
include only the assigned HTTPS hostname and localhost test origins. No proxy
headers are trusted. Uploads, unsafe HTTP methods, body-bearing GETs, custom
components, debug routes and metrics are denied. Framework assets and in-memory
media remain accessible. CORS/XSRF remain enabled.

Anonymous reconnects deliberately discard a supplied Streamlit session ID,
preserving the protocol and XSRF token. Disconnected-session TTL is zero. A
refresh/reconnect starts a fresh temporary state, including after 15 minutes.
Session identifiers are not treated as authentication credentials.

Kernel limits apply to the application worker, not the pre-existing Ollama
service, GPU memory or the Windows supervisor. The ngrok agent uses a separate
512 MiB Job Object described below. Ollama remains loopback-only
and inference has the existing bounded context, output, deadline and single slot.
There is no guarantee of four simultaneously usable analysis workers after
disconnect; cancellation may take until the in-flight operation finishes.
The memory ceiling may cause an allocation failure or loss of availability.
Shared budgets can be exhausted by one anonymous visitor. Free bandwidth quotas
can also be exhausted before local admission. This is not a DDoS SLA.

## Filesystem isolation: owner approval required

Review `tools/Install-DemoIsolation.ps1` before running it in elevated PowerShell.
It refuses an existing `AEIDemo` account or deployment directory. It copies only
the hash-reviewed publication manifest, pinned dependencies, installed Python
runtime (excluding existing site-packages), and five verified synthetic snapshots
into `C:\ProgramData\AEI-Sentinel-Demo`. It creates one standard local user
`AEIDemo`; the owner supplies its password through a secure console prompt.

Only the new deployment directory receives ACL changes. Administrators, SYSTEM
and the installing owner retain full control. AEIDemo gets ReadAndExecute;
only the two fixed runtime budget/control directories get Modify. The original
profile, repository and unrelated Windows ACLs are not changed. No token is
copied. Partial failure does not delete or retry an installation silently.

The script downloads the pinned wheels from the normal pip index; it does not
install services or start any server. After approval and installation, actual
token SID, denial of access to the owner's personal files/credentials, read-only
source/assets and permitted control writes must be tested under AEIDemo before
exposure. The isolated launcher (`python.exe -B -m src.windows_job --isolated`,
from the copied app directory under AEIDemo) refuses the developer source tree
and checks the real Windows token SID. It checks denial of listing the owner's
profile, credentials directory and original repository, and denial of opening
the private auth file and original app file. It does not read their contents.
It also checks that source, interpreter and snapshots cannot be opened for
writing and that the two budget directories permit control-file access. Missing
or mismatched checks abort before binding a listener. These checks must still
run on the actual installed copy; developer-account tests do not prove isolation.

If preparation fails before account creation, retain the partial copy. The
explicit `-Resume -PreviousPublicationManifest <previous-json>` mode verifies
its source hashes, root permissions, Python identity and synthetic snapshots
before updating the source from the current manifest. It does not download or
reinstall dependencies. It normalizes child ACLs to inherit the protected
deployment root (pip may retain temporary-directory ACLs when moving packages),
then checks all pinned package versions before prompting for a new password.
The root ACL and personal profile are not reset. An existing AEIDemo account or
isolation marker stops this recovery path; it never deletes an account or copy.
Preparation invokes `-m tools.prepare_demo_runtime`, avoiding inline Python
string quoting differences in Windows PowerShell 5.1. No ExecutionPolicy change
is made by the installer.

## Owner operations and emergency stop

### Isolated startup and owner operations

`tools/Start-IsolatedDemo.ps1` prompts securely for AEIDemo credentials and opens
its local console. It does not save a password or open a tunnel. Keep that console
open. The launcher validates the real account SID and kernel limits before bind.
It reconstructs USERPROFILE, HOME, APPDATA, LOCALAPPDATA and TEMP from the actual
Windows account profile, discarding inherited environment variables, tokens,
proxy settings, Python paths and analytical overrides. SQLite sorting uses the
account's own ephemeral Temp directory. No personal-profile permission is added.
The owner console disables PSReadLine history only for that session to avoid
writing to the caller's history path. No ExecutionPolicy setting is changed.

After downloading the reviewed agent, run `tools/Configure-DemoNgrok.ps1 -Agent
<absolute-ngrok-exe>` as the owner. Paste the authtoken only into its hidden input.
The fixed private configuration and reviewed binary go into
`C:\ProgramData\AEI-Sentinel-Demo\ngrok`, outside either project source tree.
Owner, SYSTEM and Administrators retain full control; AEIDemo has read access
for its agent. The app and agent share this dedicated service identity. The
service token is therefore accessible to that identity, not to public routes;
the owner's personal credentials remain inaccessible. Configuration is never
echoed and is never overwritten implicitly. Config validation does not establish
successful provider authentication; that remains a first-connect check.

Agent inspection/database, local web API, local logging, remote management and
automatic update checks are disabled. Provider-side TLS termination and provider
metadata handling still apply; these local options are not a claim that ngrok
stores no metadata. No optional Traffic Policy or paid feature is enabled.

`tools/Start-IsolatedTunnel.ps1` defaults to a check only. It checks the exact
binary digest, strict private profile, worker image/command/SID and local health.
Its AEIDemo console can remain open for later owner operation. An actual start
requires explicit final owner approval and the separate
`--approved-public-exposure` mode of `tools.run_demo_tunnel`; the PowerShell
wrapper also asks the owner to type OPEN when its exposure switch is used.
Never run either exposure mode during preparation.

The agent has its own Job Object: 512 MiB committed memory, 25% aggregate CPU,
one process, killed when its supervisor closes. Application and agent bounds
are separate; their Python supervisors and Windows consoles add small overhead.
Ollama/GPU memory remains outside those jobs. No permanent service is installed.
Ctrl+C in the agent console stops its owned job and endpoint. Ctrl+C in the app
console stops its owned application job. External closure and same-domain restart
passed after owner approval. Recheck them after changing the deployment.

1. Approve and validate the new local account and filesystem isolation.
2. Configure ngrok token privately, outside Git. Disable inspection, persistent
   request logs, local inspection API and remote management where supported;
   validate the configuration before starting an endpoint.
3. Review exact hostname, current quotas, interstitial and unresolved risks.
4. Obtain explicit final owner approval immediately before any public endpoint.
5. Run a bounded external browser test on an independent network, including
   HTTPS, WS, Manual/AI Search, reports, two visitors, denial checks, fresh-state
   reconnect and controlled tunnel restart using the same assigned hostname.
6. Audit the final publication manifest and ask separately before commit/push.

Once an endpoint is approved and running, stop its own foreground ngrok process
with Ctrl+C to close public access immediately; verify closure externally. Stop
the owned application supervisor separately. Do not kill unrelated processes.
If a token is suspected compromised, the owner should revoke it in the ngrok
dashboard and stop the running agent; credential revocation alone is not claimed
to terminate every already-established connection immediately.

After a successful Manual Search the form retains its submitted query seed.
New batched edits on the next submission are not replaced by the previous
result's filters. This does not change the analytical engines or AI validator.

Public snapshots do not register the developer revision-watcher fragment. Even
an immediate return inside a timed fragment schedules browser reruns; those
unnecessary frames would exhaust the WebSocket budget and reset the session.
Developer Mode keeps its existing revision and Replay timer.

Health check: `Invoke-WebRequest http://127.0.0.1:8515/_stcore/health`.
This only checks local service liveness; it does not prove external access or AI
readiness. Recheck pinned model identity, quotas and an external visit before
submission. AC sleep and hibernate timers were disabled on inspection; no power
settings were changed. Power, Windows updates, network outages, quotas and manual
restart still affect availability. No 24/7 guarantee is made.
