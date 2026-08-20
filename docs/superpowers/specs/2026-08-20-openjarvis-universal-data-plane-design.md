# OpenJarvis Universal Data Plane Design

Date: 2026-08-20
Status: Approved for planning
Branch: `native/runtime`
Baseline: `184039a3341215e6112559e8830f44526e2f3327`

## Purpose

Add a native Universal Data Plane to OpenJarvis that can discover, synchronize,
query and directly operate structured resources exposed by websites and
providers. Menus, catalogs, branches, orders and payment status stay structured
throughout their lifecycle instead of being flattened into documents and sent
through KnowledgeStore/RAG.

The preferred execution path is direct and reusable:

```text
cached validated capability
  -> direct REST / GraphQL / provider / MCP call
  -> normalized Structured Snapshot
```

The browser is a selective discovery and fallback capability. It is used for
network observation, authentication/navigation or DOM fallback only when
publisher-declared and statically discoverable structured sources are
insufficient. A known source should normally synchronize with zero Playwright
actions.

`OrchestratorAgent` remains the only component that decides what action happens
next. The Data Plane executes one requested operation and reports evidence; it
does not become a second agent or workflow engine.

## Confirmed constraints

These decisions were approved during design and are not open in planning.

1. The Data Plane has exactly three major modules: `DiscoveryEngine`,
   `StructuredSnapshotStore` and `DirectExecutionEngine`.
2. `SourceCapability` is the persisted output of discovery, not a fourth major
   subsystem.
3. Direct structured sources are preferred over Playwright click/type
   automation. The browser is selective fallback, not a per-turn latency tax.
4. A discovered source may be used for reads only after validation. Mutation is
   enabled per provider and per operation after provider-specific validation and
   the existing policy/approval path. This is a one-time trust decision, not a
   network round trip added to every warm read.
5. Direct tools such as HTTP, shell and MCP remain available to the Agent
   through `ToolExecutor`. Their output does not automatically become a trusted
   capability or persisted snapshot.
6. Structured transactional data does not flow through `KnowledgeStore` or the
   RAG ingestion pipeline. A deliberate projection may be added later, but is
   not implicit.
7. Kiosk is presentation and device policy only: Vision WebSocket, FSM,
   microphone policy, Voice status, iframe host and localhost bridge. It is not
   a data/session/orchestration authority.
8. Jarvis may generate entirely new HTML/CSS/JavaScript. Templates are optional
   reusable seeds, not a structural restriction. Generated code runs in a
   sandboxed iframe and communicates only through a typed localhost bridge.
9. Trend Coffee is the first provider used to prove the design end to end, but
   provider-specific behavior stays behind reusable adapter seams.
10. Cold universal discovery targets roughly 60 seconds. Known-source sync
    targets roughly 5--30 seconds. The ideal warm path uses no browser action.

## Existing OpenJarvis seams to reuse

The design extends the current modular monolith rather than creating a parallel
runtime.

- `SystemBuilder` remains the composition root.
- `OrchestratorAgent` remains the reasoning and next-action authority.
- `ToolRegistry` exposes semantic Data Plane tools beside native and MCP tools.
- `ToolExecutor` retains argument parsing, boundary guard, capability policy,
  taint checks, confirmation, timeout, EventBus lifecycle and tracing.
- Skills provide policy and domain guidance such as "direct API first, browser
  fallback"; they do not contain endpoints, secrets or mutable state.
- FastAPI provides Data Plane control routes and immutable artifact routes.
- `EventBus` and tracing receive discovery, synchronization, execution and
  artifact lifecycle events.
- Scheduler/Operator may invoke read-only synchronization, but never decides or
  performs a business mutation.
- MCP remains a transport and tool adapter on the same authority path.

The existing connector sync path is intentionally not reused as the structured
store. `BaseConnector.sync()` produces documents for `IngestionPipeline` and
KnowledgeStore. Changing that contract would mix document retrieval with
versioned transactional resources. A new `SourceAdapterRegistry` follows the
same registry style without changing `BaseConnector`.

## Architecture

```text
User / Voice / Kiosk UI
          |
          v
  OrchestratorAgent       Skill policy
          |                   |
          +------ tool call <-+
          |
          v
     ToolExecutor
          |
          +---------------- native HTTP / shell / MCP / browser tools
          |
          v
  Semantic Data Plane tools
          |
          +--> DiscoveryEngine --------> SourceCapability persistence
          |          |                           |
          |          +--> Browser observer       |
          |          +--> SourceAdapterRegistry  |
          |                                      v
          +--> DirectExecutionEngine <-----------+
          |          |
          |          +--> REST / GraphQL / provider / MCP
          |          |
          |          v
          +--> StructuredSnapshotStore
          |
          +--> ArtifactWorkspace -> FastAPI localhost -> sandboxed iframe
```

## Module 1: DiscoveryEngine

### Responsibility

`DiscoveryEngine` turns a source reference such as an origin URL, provider name
or MCP service into attributable evidence and, when validation succeeds, a
reusable `SourceCapability`.

It owns:

- bounded discovery cascade and time budget;
- robots/publisher-policy evidence;
- fingerprints and provenance;
- safe candidate validation;
- adapter matching and capability compilation;
- trust-state promotion, demotion and expiry;
- persistence of complete or partial discovery results.

It does not own business sequencing, credentials, cart state, snapshot query,
or mutation execution.

### Interface

```python
class DiscoveryEngine(Protocol):
    def discover(
        self,
        source_ref: SourceRef,
        constraints: DiscoveryConstraints,
    ) -> DiscoveryResult: ...

    def refresh(self, source_id: SourceId) -> DiscoveryResult: ...

    def get_capability(
        self,
        source_id: SourceId,
    ) -> SourceCapability | None: ...
```

### Discovery cascade

The engine stops at the first level that yields an attributable, sufficiently
described and safely validated contract:

1. cached, validated `SourceCapability`;
2. known provider fingerprint;
3. publisher-declared API catalog/service links;
4. OpenAPI, GraphQL introspection or embedded structured JSON/JSON-LD;
5. static HTML and same-origin JavaScript analysis;
6. safe `GET`, `HEAD` or `OPTIONS` candidate validation;
7. browser network observation;
8. guided DOM/browser fallback.

Static bundle strings are candidates, not authority. A route-looking string
does not establish method, authentication, request schema or business meaning.

Browser observation returns evidence to `DiscoveryEngine`; it never writes a
capability or snapshot directly. Browser fallback runs in an isolated context
and must not expose cookies or literal credentials in persisted evidence.

### SourceAdapterRegistry

Provider knowledge is isolated behind a small adapter protocol:

```python
class StructuredSourceAdapter(Protocol):
    def match(self, evidence: DiscoveryEvidence) -> MatchResult: ...
    def compile(self, evidence: DiscoveryEvidence) -> SourceCapability: ...
    def normalize(
        self,
        resource_type: ResourceType,
        payload: object,
    ) -> NormalizedBatch: ...
```

Initial registrations:

- `TrendCoffeeAdapter` for the first real provider;
- generic OpenAPI/REST mapping support;
- generic GraphQL mapping support;
- generic embedded structured-data support.

Adapters convert transport payloads into normalized resources. They do not
select the next operation or bypass `ToolExecutor`.

### SourceCapability

A capability contains no secret. At minimum it records:

```text
source_id
provider identity and origin
transport kind
resource and operation contracts
HTTP method / endpoint template / schema
normalization mapping
fingerprints and schema hashes
discovery evidence and provenance
trust state per operation
validated_at / expires_at
capability revision
```

Trust lifecycle:

```text
candidate -> quarantined -> read_validated -> write_validated -> revoked
```

Trust is operation-specific. For example, `menu.read` may be validated while
`order.place` remains unavailable. A schema or origin change demotes affected
operations to `quarantined`; prior snapshots remain queryable with a stale
warning, while mutation fails closed.

## Module 2: StructuredSnapshotStore

### Responsibility

`StructuredSnapshotStore` is the authoritative local read model for normalized
structured resources. It is optimized for exact fields, versions, freshness,
provenance and deterministic queries rather than semantic document retrieval.

It owns:

- atomic resource-batch commits;
- source/resource/version identity;
- typed indexed metadata plus original normalized JSON payload;
- freshness and synchronization metadata;
- history and diff;
- last-known-good preservation;
- local deterministic queries.

### Interface

```python
class StructuredSnapshotStore(Protocol):
    def upsert(self, batch: NormalizedBatch) -> SnapshotCommit: ...
    def get(self, ref: SnapshotRef) -> StructuredSnapshot | None: ...
    def query(self, query: StructuredQuery) -> StructuredResult: ...
    def history(self, ref: ResourceRef) -> list[StructuredSnapshot]: ...
    def diff(
        self,
        older: SnapshotRef,
        newer: SnapshotRef,
    ) -> SnapshotDiff: ...
```

### Storage model

SQLite is the initial backend because it already fits OpenJarvis local
persistence and supports transactions and indexes without a new service.

Each snapshot is keyed by source, resource type, resource identity and version.
It records sync time, source capability revision, provenance and normalized
payload. Frequently queried fields such as branch, product, status and update
time receive typed indexes; the full normalized object remains JSON.

A synchronization batch commits atomically. If normalization or persistence
fails, the previous complete snapshot remains active. Document projection into
KnowledgeStore, if ever required, is an explicit downstream operation.

### Query consistency

Semantic tools expose three consistency modes:

- `cached`: query the current local snapshot only;
- `refresh_if_stale`: return a valid snapshot and trigger/await refresh based on
  policy;
- `live`: synchronize through `DirectExecutionEngine` before querying.

Local cached queries should complete in milliseconds. Read paths may use
stale-while-revalidate when the capability remains structurally valid.

## Module 3: DirectExecutionEngine

### Responsibility

`DirectExecutionEngine` executes a validated capability through a persistent
provider transport, normalizes read results and commits snapshots. For writes,
it performs one requested mutation and returns an execution receipt; observing
the resulting merchant state is a separate operation.

It owns:

- persistent HTTPX connection/cookie sessions;
- auth/credential reference resolution;
- CSRF and provider session lifecycle;
- REST, GraphQL, provider and MCP transports;
- origin/method/schema enforcement;
- safe retry and rate-limit behavior;
- normalization through the selected adapter;
- snapshot commit after successful reads;
- mutation receipts and independent verification.

It does not decide which operation to execute.

### Interface

```python
class DirectExecutionEngine(Protocol):
    def sync(
        self,
        source_id: SourceId,
        resources: list[ResourceType],
    ) -> SyncReceipt: ...

    def execute(
        self,
        source_id: SourceId,
        operation: OperationName,
        arguments: dict[str, object],
    ) -> ExecutionReceipt: ...

    def verify(self, receipt_id: ReceiptId) -> VerificationResult: ...
```

### Direct execution rules

- Safe reads may execute immediately when capability and policy checks pass.
- No non-idempotent request is retried automatically unless the provider
  contract explicitly supplies a real idempotency mechanism.
- A timeout after sending a mutation produces an `unknown` receipt, not an
  assumed failure.
- Mutation acknowledgement and state observation remain separate, preserving
  the Phase 1 mutate/observe doctrine.
- A successful read or verification may update the snapshot store atomically.
- HTTP redirect targets are revalidated against network and origin policy.

`http_request`, `shell_exec` and raw MCP tools remain useful investigative and
direct-execution capabilities. They still run through `ToolExecutor`, but do
not inherit the trust or persistence semantics of this engine.

## Semantic tools

The Data Plane is exposed to the Agent through narrow native tools:

| Tool | Purpose |
|---|---|
| `source_discover` | Discover or refresh a source and report evidence/trust |
| `source_sync` | Synchronize validated structured resources |
| `structured_query` | Query normalized snapshots with explicit consistency |
| `source_execute` | Execute one validated provider operation |
| `source_verify` | Independently observe the outcome of an execution receipt |
| `artifact_render` | Publish/activate a generated UI artifact over snapshots |

Every tool is registered in `ToolRegistry` and invoked through `ToolExecutor`.
The Agent receives compact structured results, not raw crawl pages or browser
traffic dumps.

## Cold path

```text
OrchestratorAgent calls source_discover
  -> apply discovery policy and total budget
  -> capability cache lookup
  -> provider fingerprint match
  -> API catalogs / OpenAPI / GraphQL / structured JSON
  -> static HTML and JavaScript evidence
  -> safe read-only validation
  -> browser network observation when required
  -> DOM/browser fallback when required
  -> adapter compiles and validates SourceCapability
  -> persist capability and evidence
  -> DirectExecutionEngine performs initial sync
  -> StructuredSnapshotStore atomically activates snapshot
  -> Agent reasons over DiscoveryResult / SyncReceipt
```

Indicative budget:

| Stage | Target |
|---|---:|
| policy/cache | under 1 s |
| standards discovery | 1--5 s |
| static HTML/assets | 2--10 s |
| safe candidate validation | 2--10 s |
| browser observation/fallback | at most 20 s |
| capability compile and initial sync | 5--15 s |

The overall cold attempt is bounded around 60 seconds. On budget exhaustion the
engine persists partial evidence in `candidate`/`quarantined` state and reports
what remains unknown; it does not claim a validated source.

## Warm path

```text
OrchestratorAgent calls source_sync or structured_query
  -> load validated SourceCapability
  -> local expiry/fingerprint/policy check
  -> DirectExecutionEngine reuses persistent transport
  -> REST / GraphQL / provider / MCP request
  -> adapter normalization
  -> atomic snapshot commit
  -> EventBus and trace
  -> compact result to Agent
```

The warm path must not perform browser startup, static asset analysis or model
mapping of raw provider responses. Typical synchronization target is 5--30
seconds depending on provider size and rate limits; the ideal path records zero
Playwright actions.

## Mutation and verification path

```text
Agent selects semantic mutation
  -> ToolExecutor capability/policy/confirmation
  -> bind confirmation to operation, normalized arguments and capability rev
  -> DirectExecutionEngine sends exactly one mutation
  -> return ExecutionReceipt
  -> Agent reasons again
  -> source_verify observes provider state
  -> compare observed state with confirmed request
  -> update snapshot and report result
```

This preserves direct execution without creating a second orchestrator. For an
order, confirmation covers merchant, branch, fulfillment type, variants,
quantities, notes, total, customer/delivery fields and payment consequence.
Ordering mutations execute sequentially and cannot run in parallel with an
observation of the same merchant state.

## Responsibility seams

### Agent

Chooses the next action and interprets each observation. It may use semantic
Data Plane tools or existing direct HTTP, shell, browser and MCP tools. It does
not persist trusted capabilities or snapshots itself.

### Skill

Provides orchestration policy and domain guidance: direct API first, browser
fallback, confirmation requirements and provider usage guidance. It contains
no secret, endpoint authority, transport lifecycle or mutable state.

### Browser

Acts as an internal `DiscoveryEngine` adapter for passive network observation,
interactive authentication/navigation and final DOM fallback. It returns
evidence only. Existing Playwright MCP tools can also remain explicitly
available to the Agent, but they do not silently merge into trusted discovery.

### Direct HTTP

Persistent provider HTTP belongs inside `DirectExecutionEngine`. The generic
`http_request` tool remains a separate native investigative tool. Only the
engine enforces a compiled capability and gains automatic snapshot semantics.

### Store

`StructuredSnapshotStore` stores normalized operational resources.
KnowledgeStore stores documents. Neither chooses an action or calls a provider.

### Connector

`SourceAdapterRegistry` reuses OpenJarvis's registry pattern and isolates
provider mapping. Existing `BaseConnector` and its document ingestion path stay
unchanged.

### MCP

MCP may supply discovery evidence, an execution transport or a provider
adapter. `MCPToolAdapter` still enters through `ToolExecutor`. MCP cannot become
a second orchestrator or bypass policy, approval, tracing or cancellation.

## Artifact Workspace and localhost rendering

### Responsibility

`ArtifactWorkspace` stores immutable revisions of Agent-generated HTML/CSS/JS,
validates each bundle, serves it through FastAPI localhost routes and switches
the Kiosk iframe only after the new revision is ready.

It is outside the three core Data Plane modules. `artifact_render` may read
structured snapshots and publish an artifact, but rendering does not become
data authority.

### Interface

```python
class ArtifactWorkspace(Protocol):
    def publish(
        self,
        bundle: ArtifactBundle,
        manifest: ArtifactManifest,
        previous_revision: ArtifactRevision | None = None,
    ) -> ArtifactRef: ...

    def get(
        self,
        artifact_id: ArtifactId,
        revision: ArtifactRevision | None = None,
    ) -> ArtifactBundle: ...

    def activate(self, ref: ArtifactRef) -> None: ...
    def rollback(self, artifact_id: ArtifactId) -> ArtifactRef: ...
```

An artifact bundle normally contains `index.html`, `styles.css` and `app.js`.
The manifest declares title, optional template seed, snapshot read scopes,
allowed semantic UI intents, expiry and revision. Templates such as catalog,
cart, order review/status, payment QR, table and dashboard are reusable seeds;
the Agent may replace them completely.

FastAPI serves immutable revision URLs beneath:

```text
/v1/artifacts/{artifact_id}/revisions/{revision}/...
```

A revision must pass validation and a readiness check before activation. A
failed revision leaves the previous artifact active and emits an EventBus/trace
failure. Kiosk provides only the iframe host and fallback shell.

### Localhost bridge

The Kiosk parent creates a `MessageChannel` and transfers one port to the
artifact. Every typed envelope is checked against:

- expected localhost origin and transferred port;
- artifact id and active revision;
- nonce;
- manifest read/action allowlist;
- request and response schema;
- payload size and rate limits.

Artifacts send semantic intents, never raw tool names. Pre-authorized local
display operations may query declared snapshot scopes without an LLM turn.
Business actions travel through:

```text
iframe intent
  -> Kiosk bridge
  -> FastAPI control route
  -> UIAction input
  -> OrchestratorAgent
  -> ToolExecutor
  -> Data Plane or direct tool
```

### Sandbox

The iframe uses `sandbox="allow-scripts"` without same-origin, navigation,
popups, forms, downloads, device permissions, storage or cookies. Generated
code cannot access the parent DOM or network directly.

Baseline CSP:

```text
default-src 'none'
script-src 'self'
style-src 'self'
img-src 'self' data: blob:
font-src 'self'
connect-src 'none'
frame-src 'none'
object-src 'none'
base-uri 'none'
form-action 'none'
```

No `eval`, external script/style fetch or arbitrary direct provider request is
allowed from the artifact. Data and actions use the localhost bridge.

## Security model

### Credentials and network

Capabilities persist credential references only. Tokens, cookie jars, OAuth
context and CSRF state remain backend-only and are resolved by
`DirectExecutionEngine` at call time.

All provider requests apply HTTPS-by-default, origin/endpoint allowlists,
redirect revalidation, SSRF protection, timeout, response-size, rate and
content-type/schema limits. Private network access requires explicit deployment
configuration. Traces redact credentials, cookies, payment payloads and PII.

### Prompt injection

Website text, browser observations, API payloads and generated artifacts are
untrusted data, never Skills, system instructions or approvals. External
content cannot grant tool permissions, read secrets, promote capabilities or
control the Agent. Discovery sanitizes evidence before persistence.

### Capability and approval checks

Representative capabilities are `source:discover`, `source:read`,
`source:write` and `artifact:publish`. Existing `ToolExecutor` policy remains
the enforcement point. Warm reads need only cheap local trust/schema/expiry
checks. Writes require an operation-specific `write_validated` capability and
the configured confirmation/approval policy.

## Error model

Data Plane tools return structured errors:

```text
CapabilityMissing
CapabilityQuarantined
CapabilityStale
SchemaMismatch
AuthenticationRequired
AuthenticationExpired
RateLimited
ProviderUnavailable
DiscoveryBudgetExceeded
MutationAmbiguous
VerificationFailed
ArtifactRejected
```

Safe/idempotent reads may retry with bounded backoff and `Retry-After`.
Non-idempotent writes do not retry without a provider idempotency contract.
Partial sync never replaces a complete snapshot. A schema/origin mismatch
demotes the capability and blocks mutation. Ambiguous writes require
verification or user intervention.

## Observability

Reuse EventBus and tracing with domain lifecycle events:

```text
source.discovery.started
source.discovery.stage_completed
source.discovery.completed
source.capability.validated
source.capability.demoted
source.sync.started
source.sync.committed
source.sync.failed
source.execute.started
source.execute.receipt_created
source.verify.completed
artifact.published
artifact.activated
artifact.rejected
artifact.rolled_back
```

Trace metadata includes source id, capability revision, discovery stage,
transport used, browser fallback, cache hit, snapshot version, resource count,
stage latency, retry count, receipt state and artifact revision. It excludes
secrets and unnecessary personal/provider payloads.

Key metrics are cold/warm latency, capability hit rate, zero-browser warm-path
ratio, browser fallback rate, schema demotion rate, snapshot freshness,
ambiguous mutation count and artifact rejection/rollback count.

## Scheduler and background work

Scheduler/Operator may invoke `source_sync` for validated read capabilities and
update snapshots. It cannot perform `source_execute`, confirm transactions or
choose a business action. Stale-while-revalidate is allowed for reads while the
capability remains structurally valid.

## Trend Coffee proof

Trend Coffee is the first concrete adapter and acceptance provider. Discovery
evidence already indicates public branch/product JSON plus public order/payment
routes in the first-party application bundle. Planning must still treat bundle
strings as candidates and revalidate the live contract.

The end-to-end proof is:

```text
cold discovery
  -> validated Trend Coffee read capability
  -> initial branch/menu snapshot
  -> generated HTML/CSS/JS artifact
  -> sandboxed iframe render in Kiosk
  -> user requests two black coffees, takeaway
  -> exact order confirmation
  -> one direct provider mutation
  -> independent order/payment verification
  -> merchant-returned QR rendered in the artifact
```

The QR payload must come from a verified merchant/payment response. The Agent
must never invent payment data or author the QR payload from prose.

After restart, the warm acceptance path loads the persisted capability, syncs
directly, updates the snapshot/artifact and records zero Playwright actions.

## Verification strategy

### Discovery

Fixture tests cover OpenAPI, GraphQL, JSON-LD, SPA bundles, provider
fingerprints, network-observation-only sources, DOM fallback, robots denial,
redirect/SSRF attempts and changed schemas/origins. Trend Coffee captured
fixtures prove its adapter without requiring a live mutation.

### StructuredSnapshotStore

Tests cover atomic commit, version/history/diff, deterministic query, concurrent
reads, provenance, migration and preservation of the last complete snapshot on
failure.

### DirectExecutionEngine

Fake REST/GraphQL/MCP transports cover persistent cookies, auth/CSRF,
pagination, schema validation, rate limiting, safe retries, absence of implicit
POST retries, execution receipts and separate verification.

### Runtime integration

Integration tests prove that `OrchestratorAgent` alone sequences operations,
Skills only influence policy, every operation enters through `ToolExecutor`,
the warm path avoids the browser, mutation is sequential, and direct native
tools cannot silently persist trusted capabilities.

### Artifact security

Tests exercise malicious bundles, CSP violations, bridge spoofing, wrong
nonce/revision, undeclared intents, oversized payloads, activation failure and
rollback. A local declared read may avoid a model turn; every business mutation
must return through the Agent.

### Live acceptance

An authorized Trend Coffee run verifies real branch/menu data, exact order
preview, one mutation, independent verification and merchant-originated QR.
Automated suites do not create real orders or payments. Browser/WebSocket
evidence is required for the generated artifact and Kiosk bridge; unit tests
alone are insufficient.

Performance acceptance:

- cold discovery aims to finish within roughly 60 seconds;
- known-source sync normally finishes within 5--30 seconds;
- cached snapshot queries complete locally in milliseconds;
- ideal warm synchronization records `browser_actions = 0`.

## Correction to the previous goal-execution design

The earlier
`docs/superpowers/specs/2026-08-19-one-jarvis-goal-execution-design.md`
suggested discovering request schemas using deliberately incomplete production
`POST` requests. This design supersedes that technique.

An invalid `POST` is still unsafe: a provider may log, reserve, enqueue,
rate-charge or partially apply the request before returning a validation error.
Discovery therefore uses publisher metadata, static analysis, safe read-only
validation and passive network observation. Mutation probing is permitted only
against an authorized sandbox/staging environment with documented guarantees.

## Non-goals

- No new workflow/orchestration engine.
- No generic cross-provider transaction language in the first implementation.
- No claim that every website can be safely mutated after automatic discovery.
- No forced Playwright startup for known sources or casual Agent turns.
- No replacement of KnowledgeStore, BaseConnector or MCP.
- No Kiosk-owned business/session state.
- No unrestricted network or parent-DOM access for generated artifacts.
- No real payment/order mutation in unattended automated tests.
- No production implementation in this design phase.

## Planning implications

Implementation planning should deliver this incrementally behind the approved
interfaces. It should start with the smallest vertical proof: structured store,
HTTP-first Trend Coffee read discovery/sync, generated sandboxed artifact, then
confirmed execution/verification. Generic discovery stages and browser fallback
follow without changing Agent authority or the warm direct path.

The plan must classify every item as either a new module/artifact or a surgical
modification to an existing OpenJarvis seam, and must preserve unrelated dirty
worktree state.
