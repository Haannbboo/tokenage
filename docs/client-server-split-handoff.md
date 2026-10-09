# Hosted client/server split: handover

Status: design agreed; the split and independent releases shipped. Hosted login and authenticated OTLP ingestion already exist. This note records the design direction for the hosted product.

## Ownership

| Machine client | Hosted server |
| --- | --- |
| Install/update, login, device credentials, agent configuration, OpenCode/Kilo plugins | Sign-in, token issuance, deployment |
| Background evaluation worker and local transcript access | Evaluation jobs, result validation and storage |
| Loopback proxy, provider keys, usage reports | Usage-report ingestion, pricing and usage storage |
| Command integration and local status checks | OTLP ingestion, usage APIs, dashboard |

The dashboard belongs to the server release. Agents send OTLP directly to the hosted collector. Hosted proxy tracking uses **only the machine-local proxy**: provider traffic and keys remain on the machine; the server receives usage metadata. Hosted evaluations execute on the machine with the transcript and report structured results. These decisions supersede earlier hosted-gateway and hosted-evaluation-deferral plans.

## Packages, versions and installation

Keep one repo with installable `client/` and `server/` packages plus a small `protocol/` package for wire schemas. Dependencies point client → protocol ← server; neither deployable package imports the other. Root `VERSION` becomes the server version; add `client/VERSION`. Release and bump them independently. Server `/version` reports the server release; the client CLI and local proxy report the client release. The server accepts the current and previous protocol generations, and the client checks compatibility at login/update. Report the installed client version per device in the dashboard.

The first client installer supports macOS and Linux. It manages Python and installs a client package. During login, plugin setup builds OpenCode and Kilo plugins with npm. Users need npm and network access for those plugins. Preserve the existing self-hosted install during migration. The client notifies users about updates; `tokenage update` runs only when requested and preserves credentials and local provider settings.

## User flow

After hosted sign-in, the user installs the client on each machine and runs `tokenage login --server <hosted URL>`. The existing browser/code exchange provides separate CLI and ingestion tokens. Login configures detected agents and starts a user-level background service for evaluations and the local proxy; that service starts again at user login. The proxy binds to loopback and starts automatically, but apps use it only after the user points them at it. The hosted dashboard shows usage, evaluations, and client versions across the user's devices.

## Data contracts

- **Proxy:** Add `POST /ingest/v1/usage` to the existing ingestion service. Authenticate with the device ingestion token. Events carry a schema version, unique event ID, provider/model, token counts, status and timings. They exclude prompts, responses, tool arguments/results, provider keys, user identity and client-calculated cost. The server derives the user, calculates cost and acknowledges after storage. The client makes a single attempt per report and drops it on failure; there is no retry queue. Proxy/OTLP overlap still needs best-effort correlation until both sources share a request ID.
- **Evaluation:** Automatic evaluation is enabled by default per device. Login explains that it uses local Codex or Claude credits and offers an off switch. The server assigns idle-session and dashboard-requested jobs to the device that produced the session. Its background worker claims the job, reads the local transcript, runs the evaluator, and uploads bounded outcome, confidence, title, summary, evidence and failure reason. The server validates device/job ownership and stores results idempotently. Full transcripts and evaluator credentials stay local; derived text needs length limits and secret scrubbing. Jobs wait while the device is unavailable.

## Migration

Split current `src/cli.py`, `src/proxy.py`, `src/evaluation.py`, `src/evaluation_worker.py` and install scripts by ownership, keeping compatibility entrypoints for self-hosted users. Add stable device identity across token rotation and authenticated evaluation job APIs. Update CI so client-only changes do not bump the server version, and vice versa. Specify package format, queue limits, payload schema and job lease/retry behavior in implementation specs.
