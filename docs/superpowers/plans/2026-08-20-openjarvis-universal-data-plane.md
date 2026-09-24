# OpenJarvis Universal Data Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an HTTP-first Universal Data Plane that discovers and persists structured provider capabilities, synchronizes/query snapshots, directly executes validated Trend Coffee operations, and renders Agent-generated sandboxed artifacts inside Kiosk.

**Architecture:** Add three focused modules under `openjarvis.data_plane`: `DiscoveryEngine`, `StructuredSnapshotStore`, and `DirectExecutionEngine`. Compose them through the existing `SystemBuilder`, expose them as semantic `BaseTool` implementations through `ToolRegistry`/`ToolExecutor`, retain `OrchestratorAgent` as the only next-action authority, and add `ArtifactWorkspace` as a separate presentation module served by FastAPI and hosted by Kiosk.

**Tech Stack:** Python 3.11+, dataclasses, SQLite/WAL, HTTPX, stdlib `html.parser`/`json`/`hashlib`, optional Playwright extra, FastAPI, React 19, TypeScript 5.7, `MessageChannel`, Vitest, pytest/respx.

**Spec:** `docs/superpowers/specs/2026-08-20-openjarvis-universal-data-plane-design.md`

## Global Constraints

- `OrchestratorAgent` is the only component that decides what action happens next.
- The Data Plane has exactly three major modules: `DiscoveryEngine`, `StructuredSnapshotStore`, and `DirectExecutionEngine`; helper stores/adapters are owned by those modules.
- Prefer cached validated capability, known provider fingerprint, OpenAPI/GraphQL/embedded JSON, and static analysis before browser network observation or DOM fallback.
- A warm known-source sync must perform zero Playwright actions and target 5--30 seconds; cold discovery has a hard 60-second wall-clock budget.
- Discovery performs only `GET`, `HEAD`, and `OPTIONS`; never probe a production provider with an invalid mutation.
- Discovered write operations remain `quarantined` until provider validation and operator trust configuration promote that exact source/operation/fingerprint.
- Every business mutation passes through `ToolExecutor`, requires an approved exact request hash, executes once, returns a receipt, and is verified in a separate Agent turn.
- Never automatically retry a non-idempotent request without a provider-declared idempotency contract.
- Structured transactional data stays out of `KnowledgeStore`/RAG unless a future explicit projection is requested.
- Existing `BaseConnector` and document-ingestion contracts remain unchanged; `SourceAdapterRegistry` only reuses the registry pattern and never feeds snapshots into Connector/RAG implicitly.
- Treat page text, JSON, browser observations, and generated artifacts as untrusted prompt-injection-bearing data; none may grant permissions, become Skill/system instructions, or promote trust.
- `FakeMerchant` remains only as a deterministic test fixture; production defaults and `ordering-kiosk.toml` must not instantiate it.
- Kiosk owns only Vision WebSocket/FSM, microphone/Voice policy, artifact iframe, and localhost bridge.
- Generated HTML/CSS/JS may be entirely new; templates are optional seeds.
- Artifact iframe uses `sandbox="allow-scripts"`, CSP `connect-src 'none'`, and no same-origin, forms, navigation, popups, downloads, device permissions, storage, or cookies.
- Implementation clarification: an `allow-scripts` iframe without `allow-same-origin` has opaque origin `"null"`. Validate the exact `iframe.contentWindow`, artifact URL held by the parent, nonce, revision, transferred `MessagePort`, schemas, allowlists, and rate/size limits; do not weaken the sandbox to make `event.origin` equal localhost.
- QR data must be copied from a validated merchant payment response; the Agent may not author a QR payload.
- Preserve unrelated dirty state (`uv.lock`, `.codegraph/`, and `docs/architecture/merchant-discovery-stack-research.md`) and stage only task-owned files.
- Run pytest by focused file/directory; do not use the known memory-heavy monolithic whole-tree invocation.

## Scope and delivery shape

This spec spans storage, discovery/execution, provider integration, and artifact presentation. They are kept in one plan because the acceptance target is one vertical order flow, but Tasks 1--7 produce a complete read-only Data Plane before Tasks 8--14 add production ordering, browser fallback, generated UI, and live acceptance. A reviewer may reject any task without invalidating the prior task's deliverable.

## File structure

### New Data Plane package

- `src/openjarvis/data_plane/types.py` — immutable contracts, enums, receipts, query objects, JSON serialization helpers.
- `src/openjarvis/data_plane/errors.py` — structured error codes and `DataPlaneError`.
- `src/openjarvis/data_plane/adapters/__init__.py` — `StructuredSourceAdapter` protocol and adapter lookup.
- `src/openjarvis/data_plane/capability_store.py` — SQLite persistence owned by discovery.
- `src/openjarvis/data_plane/snapshot_store.py` — atomic versioned structured snapshot storage/query/history/diff.
- `src/openjarvis/data_plane/discovery_http.py` — bounded safe HTTP fetch, redirect/SSRF/origin/size enforcement.
- `src/openjarvis/data_plane/discovery.py` — cold cascade, cache, evidence, trust promotion/demotion and browser fallback port.
- `src/openjarvis/data_plane/execution.py` — persistent HTTPX sessions, sync/execute/verify, receipts and retry semantics.
- `src/openjarvis/data_plane/adapters/generic.py` — OpenAPI, GraphQL, JSON-LD and embedded-JSON evidence extraction.
- `src/openjarvis/data_plane/adapters/trendcoffee.py` — Trend Coffee fingerprint, contracts and normalization.

### New provider and tool integration

- `src/openjarvis/merchants/trendcoffee.py` — real `MerchantPort` facade over snapshots/direct execution plus the process-local draft cart accepted by Phase 1.
- `src/openjarvis/tools/data_plane.py` — `source_discover`, `source_sync`, `structured_query`, `source_execute`, `source_verify`.
- `src/openjarvis/data_plane/approval.py` — exact-request hash and existing `ApprovalStore` adapter used by `source_execute`.

### New artifact presentation package

- `src/openjarvis/artifacts/types.py` — bundle, manifest, ref and UI-intent contracts.
- `src/openjarvis/artifacts/workspace.py` — immutable revisions, validation, activation and rollback.
- `src/openjarvis/artifacts/routes.py` — immutable asset/read/action FastAPI routes.
- `src/openjarvis/tools/artifact_render.py` — Agent-facing publish/activate tool.
- `frontend/src/hooks/useArtifactHost.ts` — WebSocket activation listener, iframe URL and `MessageChannel` bridge.
- `frontend/src/hooks/useArtifactHost.test.ts` — bridge validation and routing tests.

### Surgical modifications

- `src/openjarvis/core/registry.py` — add `SourceAdapterRegistry` following `RegistryBase`.
- `src/openjarvis/core/config.py` — add `[data_plane]`; change merchant production default to `none`.
- `src/openjarvis/core/events.py` — add source/artifact lifecycle event types.
- `src/openjarvis/system/bundles.py`, `src/openjarvis/system/core.py`, `src/openjarvis/system/builder.py` — compose/own/close Data Plane and inject dependencies.
- `src/openjarvis/cli/serve.py`, `src/openjarvis/server/app.py`, `src/openjarvis/server/ws_bridge.py` — pass runtime dependencies, mount artifact routes and forward activation events.
- `src/openjarvis/tools/browser.py` — extend the existing Playwright owner with passive network observation; do not create a second browser controller.
- `configs/openjarvis/examples/ordering-kiosk.toml`, `configs/openjarvis/prompts/ordering-kiosk.md`, `skills/serve-trend-coffee-customers/SKILL.md` — real provider preset and direct-first Agent policy.
- `frontend/src/pages/KioskPage.tsx`, `frontend/vite.config.ts` — host active artifact instead of fixed `display.html` while preserving Voice/Vision/FSM behavior.
- Delete `frontend/public/display.html` after the artifact host is covered.

---

### Task 1: Define Data Plane contracts, errors, events, and adapter registry

**Files:**
- Create: `src/openjarvis/data_plane/__init__.py`
- Create: `src/openjarvis/data_plane/types.py`
- Create: `src/openjarvis/data_plane/errors.py`
- Create: `src/openjarvis/data_plane/adapters/__init__.py`
- Modify: `src/openjarvis/core/registry.py:129-183`
- Modify: `src/openjarvis/core/events.py:21-83`
- Test: `tests/data_plane/test_contracts.py`
- Test: `tests/data_plane/test_adapter_registry.py`

**Interfaces:**
- Consumes: existing `RegistryBase`, `EventType`, standard-library dataclasses/enums.
- Produces: `SourceRef`, `DiscoveryConstraints`, `TrustState`, `TransportKind`, `ConsistencyMode`, `ReceiptStatus`, `OperationContract`, `SourceCapability`, `DiscoveryEvidence`, `DiscoveryResult`, `ResourceRecord`, `NormalizedBatch`, `SnapshotRef`, `SnapshotCommit`, `SnapshotDiff`, `StructuredQuery`, `StructuredResult`, `SyncReceipt`, `ExecutionReceipt`, `VerificationResult`, `StructuredSourceAdapter`, and `SourceAdapterRegistry`.

- [ ] **Step 1: Write failing contract and registry tests**

```python
def test_source_capability_round_trips_without_secrets():
    capability = SourceCapability(
        source_id="trend-coffee",
        provider="trendcoffee",
        origin="https://trendcoffee.net",
        base_url="https://trendcoffee.net/api/latest",
        auth_mode="none",
        credential_ref="",
        transport=TransportKind.REST,
        operations={
            "menu.list": OperationContract(
                name="menu.list", method="GET", path="/products",
                resource_type="menu_item", trust=TrustState.READ_VALIDATED,
                safe=True,
            )
        },
        fingerprint="sha256:bundle",
        schema_hash="sha256:schema",
        evidence=(), validated_at="2026-08-20T00:00:00+00:00",
        expires_at="2026-08-21T00:00:00+00:00", revision=1,
    )
    encoded = capability.to_dict()
    assert "actual-secret-value" not in json.dumps(encoded)
    assert SourceCapability.from_dict(encoded) == capability


def test_source_adapter_registry_creates_registered_adapter():
    @SourceAdapterRegistry.register("fixture")
    class FixtureAdapter:
        pass

    assert isinstance(SourceAdapterRegistry.create("fixture"), FixtureAdapter)
```

- [ ] **Step 2: Run the focused tests and confirm missing imports**

Run: `uv run pytest tests/data_plane/test_contracts.py tests/data_plane/test_adapter_registry.py -q`

Expected: collection fails because `openjarvis.data_plane` and `SourceAdapterRegistry` do not exist.

- [ ] **Step 3: Add exact immutable contracts and structured errors**

```python
class TrustState(str, Enum):
    CANDIDATE = "candidate"
    QUARANTINED = "quarantined"
    READ_VALIDATED = "read_validated"
    WRITE_VALIDATED = "write_validated"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class OperationContract:
    name: str
    method: str
    path: str
    resource_type: str
    trust: TrustState
    safe: bool
    request_schema: dict[str, object] = field(default_factory=dict)
    response_schema: dict[str, object] = field(default_factory=dict)
    verify_operation: str = ""
    idempotency_header: str = ""
    csrf_cookie: str = ""
    csrf_header: str = ""


class DataPlaneError(RuntimeError):
    def __init__(self, code: DataPlaneErrorCode, message: str, **details: object):
        super().__init__(message)
        self.code = code
        self.details = details
```

Implement explicit `to_dict()`/`from_dict()` for persisted dataclasses; encode enum values and tuples deterministically. Define every spec error code, including `capability_missing`, `schema_mismatch`, `mutation_ambiguous`, and `artifact_rejected`.

- [ ] **Step 4: Add `SourceAdapterRegistry` and lifecycle events**

```python
class SourceAdapterRegistry(RegistryBase[Any]):
    """Provider/format adapters for structured source evidence."""
```

Add every Data Plane/Artifact `EventType` value using the spec's dotted wire names:

```python
SOURCE_DISCOVERY_STARTED = "source.discovery.started"
SOURCE_DISCOVERY_STAGE_COMPLETED = "source.discovery.stage_completed"
SOURCE_DISCOVERY_COMPLETED = "source.discovery.completed"
SOURCE_CAPABILITY_VALIDATED = "source.capability.validated"
SOURCE_CAPABILITY_DEMOTED = "source.capability.demoted"
SOURCE_SYNC_STARTED = "source.sync.started"
SOURCE_SYNC_COMMITTED = "source.sync.committed"
SOURCE_SYNC_FAILED = "source.sync.failed"
SOURCE_EXECUTE_STARTED = "source.execute.started"
SOURCE_EXECUTE_RECEIPT_CREATED = "source.execute.receipt_created"
SOURCE_VERIFY_COMPLETED = "source.verify.completed"
ARTIFACT_PUBLISHED = "artifact.published"
ARTIFACT_ACTIVATED = "artifact.activated"
ARTIFACT_REJECTED = "artifact.rejected"
ARTIFACT_ROLLED_BACK = "artifact.rolled_back"
```

- [ ] **Step 5: Run tests and lint**

Run: `uv run pytest tests/data_plane/test_contracts.py tests/data_plane/test_adapter_registry.py -q`

Expected: PASS.

Run: `uv run ruff check src/openjarvis/data_plane src/openjarvis/core/registry.py src/openjarvis/core/events.py tests/data_plane/test_contracts.py tests/data_plane/test_adapter_registry.py`

Expected: PASS.

- [ ] **Step 6: Commit the contracts**

```bash
git add src/openjarvis/data_plane src/openjarvis/core/registry.py src/openjarvis/core/events.py tests/data_plane
git commit -m "feat(data-plane): define structured source contracts"
```

### Task 2: Persist capabilities and trust transitions in SQLite

**Files:**
- Create: `src/openjarvis/data_plane/capability_store.py`
- Test: `tests/data_plane/test_capability_store.py`

**Interfaces:**
- Consumes: `SourceCapability`, `TrustState`, `OperationContract` from Task 1.
- Produces: `SQLiteCapabilityStore(db_path)`, `.save(capability)`, `.get(source_id)`, `.demote(source_id, operation_names, reason)`, `.close()`.

- [ ] **Step 1: Write failing persistence and demotion tests**

```python
def test_save_get_and_demote_are_durable(tmp_path, trend_capability):
    path = tmp_path / "structured.db"
    store = SQLiteCapabilityStore(path)
    store.save(trend_capability)
    store.demote("trend-coffee", ["order.place"], "schema_changed")
    store.close()

    reopened = SQLiteCapabilityStore(path)
    saved = reopened.get("trend-coffee")
    assert saved is not None
    assert saved.operations["menu.list"].trust is TrustState.READ_VALIDATED
    assert saved.operations["order.place"].trust is TrustState.QUARANTINED
    assert saved.revision == trend_capability.revision + 1
```

Also assert that JSON containing `authorization`, `cookie`, `token`, or `secret` keys is rejected before persistence.

- [ ] **Step 2: Verify red**

Run: `uv run pytest tests/data_plane/test_capability_store.py -q`

Expected: FAIL with `ModuleNotFoundError: openjarvis.data_plane.capability_store`.

- [ ] **Step 3: Implement WAL-backed capability persistence**

```sql
CREATE TABLE IF NOT EXISTS source_capabilities (
    source_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    origin TEXT NOT NULL,
    base_url TEXT NOT NULL,
    revision INTEGER NOT NULL,
    trust_json TEXT NOT NULL,
    capability_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    validated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    demotion_reason TEXT NOT NULL DEFAULT ''
);
```

Use `sqlite3.connect(str(db_path), check_same_thread=False)`, `PRAGMA journal_mode=WAL`, a `threading.RLock`, one transaction per save/demotion, and canonical JSON (`sort_keys=True`, compact separators).

Create a `data_plane_schema` table with one integer version. Test an empty
database migration to version 1 and reopening version 1 without destructive
DDL; later tasks add their tables through ordered migrations in the same
database.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/data_plane/test_capability_store.py -q`

Expected: PASS, including reopen durability and secret rejection.

- [ ] **Step 5: Commit**

```bash
git add src/openjarvis/data_plane/capability_store.py tests/data_plane/test_capability_store.py
git commit -m "feat(data-plane): persist source capabilities"
```

### Task 3: Build the atomic Structured Snapshot Store

**Files:**
- Create: `src/openjarvis/data_plane/snapshot_store.py`
- Test: `tests/data_plane/test_snapshot_store.py`

**Interfaces:**
- Consumes: `NormalizedBatch`, `ResourceRecord`, `StructuredQuery`, `StructuredResult` from Task 1.
- Produces: `StructuredSnapshotStore.upsert(batch)`, `.get(ref)`, `.query(query)`, `.history(ref)`, `.diff(older, newer)`, `.close()`.

- [ ] **Step 1: Write failing atomic/version/query tests**

```python
def test_upsert_versions_and_queries_current_snapshot(tmp_path):
    store = StructuredSnapshotStore(tmp_path / "structured.db")
    first = store.upsert(menu_batch(price=35_000, synced_at="2026-08-20T00:00:00Z"))
    second = store.upsert(menu_batch(price=36_000, synced_at="2026-08-20T01:00:00Z"))
    result = store.query(StructuredQuery(
        source_id="trend-coffee", resource_type="menu_item",
        filters={"name": "Cà phê đen"}, consistency=ConsistencyMode.CACHED,
    ))
    assert second.version == first.version + 1
    assert result.items[0].payload["price"] == 36_000
    assert store.diff(first.ref, second.ref).changed_ids == ("23f99adf51",)


def test_failed_batch_keeps_last_complete_version(tmp_path):
    store = StructuredSnapshotStore(tmp_path / "structured.db")
    committed = store.upsert(menu_batch(price=35_000))
    with pytest.raises(ValueError, match="duplicate_resource_id"):
        store.upsert(batch_with_duplicate_ids())
    assert store.query(current_menu_query()).version == committed.version
```

- [ ] **Step 2: Verify red**

Run: `uv run pytest tests/data_plane/test_snapshot_store.py -q`

Expected: FAIL because `snapshot_store.py` is absent.

- [ ] **Step 3: Implement schema and atomic activation**

Use tables `snapshot_versions`, `snapshot_resources`, and `snapshot_heads`. Insert a full batch and update `snapshot_heads` inside one `BEGIN IMMEDIATE` transaction. Index `(source_id, resource_type, resource_id)`, `(source_id, resource_type, version)`, and typed columns `branch`, `name`, `status`, `updated_at`.

```python
def upsert(self, batch: NormalizedBatch) -> SnapshotCommit:
    ids = [record.resource_id for record in batch.records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_resource_id")
    with self._lock, self._conn:
        version = self._next_version(batch.source_id, batch.resource_type)
        self._insert_version_and_records(batch, version)
        self._activate_head(batch.source_id, batch.resource_type, version)
    return SnapshotCommit(
        ref=SnapshotRef(batch.source_id, batch.resource_type, version),
        version=version,
        resource_count=len(batch.records),
        synced_at=batch.synced_at,
    )
```

- [ ] **Step 4: Add history/diff and concurrent-read coverage**

Use two store instances against the same WAL database; assert one reader sees the old head until commit and the new complete head after commit, never a partial batch.

- [ ] **Step 5: Run focused tests and lint**

Run: `uv run pytest tests/data_plane/test_snapshot_store.py -q`

Expected: PASS.

Run: `uv run ruff check src/openjarvis/data_plane/snapshot_store.py tests/data_plane/test_snapshot_store.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/openjarvis/data_plane/snapshot_store.py tests/data_plane/test_snapshot_store.py
git commit -m "feat(data-plane): add structured snapshot store"
```

### Task 4: Implement safe HTTP discovery and generic structured extraction

**Files:**
- Create: `src/openjarvis/data_plane/discovery_http.py`
- Create: `src/openjarvis/data_plane/discovery.py`
- Create: `src/openjarvis/data_plane/adapters/generic.py`
- Create: `tests/data_plane/fixtures/generic/index.html`
- Create: `tests/data_plane/fixtures/generic/openapi.json`
- Create: `tests/data_plane/fixtures/generic/graphql.json`
- Create: `tests/data_plane/fixtures/generic/jsonld.html`
- Create: `tests/data_plane/fixtures/generic/spa.js`
- Create: `tests/data_plane/fixtures/generic/robots-denied.txt`
- Test: `tests/data_plane/test_discovery_http.py`
- Test: `tests/data_plane/test_discovery.py`

**Interfaces:**
- Consumes: Task 1 contracts/adapter protocol, Task 2 capability store, existing `check_ssrf`, HTTPX.
- Produces: `DiscoveryHttpClient.fetch(url, method="GET")`, `BrowserObservationPort.observe(origin, timeout_seconds)`, `DiscoveryEngine.discover(source_ref, constraints)`, `.refresh(source_id)`, `.get_capability(source_id)`.

- [ ] **Step 1: Write failing HTTP safety tests**

```python
@respx.mock
def test_discovery_http_allows_only_safe_methods_and_rechecks_redirects():
    client = DiscoveryHttpClient(max_response_bytes=1024)
    with pytest.raises(DataPlaneError) as exc:
        client.fetch("https://example.test/orders", method="POST")
    assert exc.value.code is DataPlaneErrorCode.DISCOVERY_UNSAFE_METHOD

    respx.get("https://example.test/").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/"})
    )
    with patch("openjarvis.data_plane.discovery_http.check_ssrf", side_effect=[None, "private"]):
        with pytest.raises(DataPlaneError, match="redirect"):
            client.fetch("https://example.test/")
```

- [ ] **Step 2: Write failing cascade tests**

```python
def test_cached_validated_capability_short_circuits_network(engine, capability_store):
    capability_store.save(valid_read_capability())
    result = engine.discover(SourceRef("https://example.test"), DiscoveryConstraints())
    assert result.cache_hit is True
    assert result.browser_actions == 0
    engine.http.fetch.assert_not_called()


def test_html_discovers_declared_openapi_and_jsonld(engine):
    result = engine.discover(SourceRef("https://example.test"), DiscoveryConstraints())
    kinds = {e.kind for e in result.evidence}
    assert {"service_desc", "json_ld"} <= kinds
    assert all(call.method in {"GET", "HEAD", "OPTIONS"} for call in engine.http.calls)


def test_refresh_demotes_changed_schema_before_any_write(engine, capability_store):
    capability_store.save(valid_write_capability(schema_hash="sha256:old"))
    engine.http.responses = changed_schema_responses()
    refreshed = engine.refresh("trend-coffee")
    assert refreshed.capability.operations["order.place"].trust is TrustState.QUARANTINED
    assert refreshed.error_code == "schema_mismatch"
```

- [ ] **Step 3: Verify red**

Run: `uv run pytest tests/data_plane/test_discovery_http.py tests/data_plane/test_discovery.py -q`

Expected: FAIL on missing discovery modules.

- [ ] **Step 4: Implement bounded safe HTTP and structured parsers**

`DiscoveryHttpClient` must set an honest `User-Agent: OpenJarvis/structured-discovery`, follow at most five redirects manually, call `check_ssrf` for every hop, require HTTPS unless explicitly configured, enforce same-origin asset analysis, stop reading after `max_response_bytes`, and honor `Retry-After` without exceeding the discovery deadline.

`generic.py` extracts:

```python
SUPPORTED_LINK_RELS = {"api-catalog", "service-desc", "service-doc"}
JSON_LD_TYPES = {"Restaurant", "Menu", "MenuItem", "Product", "Offer"}
ROUTE_PATTERN = re.compile(r"[\"'](/(?:api/)?[A-Za-z0-9_./{}:-]+)[\"']")
```

GraphQL discovery uses publisher-declared endpoints and a URL-encoded GET introspection query only; it does not send a POST.

Parse `robots.txt` and publisher policy as attributable evidence before static
asset crawling. A denied path is not fetched. Add parameterized fixture tests
for OpenAPI, GraphQL, JSON-LD, embedded JSON, SPA route candidates, robots
denial, cross-origin redirects, and a changed origin/schema. Only the first
four may compile a generic read capability without additional evidence; SPA
route strings remain candidates.

- [ ] **Step 5: Implement the ordered cascade and deadline behavior**

```python
for stage in self._stages:
    if self._clock() >= deadline:
        return self._persist_partial(
            source_ref, evidence, DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED
        )
    stage_result = stage.run(source_ref, evidence, deadline)
    evidence.extend(stage_result.evidence)
    capability = self._compile_validated(evidence)
    if capability is not None:
        self._store.save(capability)
        return DiscoveryResult(
            source_id=capability.source_id,
            state=TrustState.READ_VALIDATED,
            capability=capability,
            evidence=tuple(evidence),
            elapsed_ms=int((self._clock() - started_at) * 1000),
            browser_actions=browser_actions,
            cache_hit=False,
            error_code="",
        )
```

Persist partial evidence as `candidate`/`quarantined`; never promote route strings without safe response/schema validation.

Mark every external evidence record `untrusted=True`, retain only bounded
publisher URL/content-type/schema samples/hashes, and discard strings that
claim to be system instructions, request credentials, or request capability
promotion. The Agent receives a compact evidence summary, not page prose.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/data_plane/test_discovery_http.py tests/data_plane/test_discovery.py -q`

Expected: PASS, including 60-second fake-clock cutoff and zero browser calls on cache hit.

```bash
git add src/openjarvis/data_plane tests/data_plane
git commit -m "feat(data-plane): add HTTP-first source discovery"
```

### Task 5: Add the Trend Coffee adapter and evidence-backed fixtures

**Files:**
- Create: `src/openjarvis/data_plane/adapters/trendcoffee.py`
- Create: `tests/data_plane/fixtures/trendcoffee/home.html`
- Create: `tests/data_plane/fixtures/trendcoffee/branch.json`
- Create: `tests/data_plane/fixtures/trendcoffee/products.json`
- Create: `tests/data_plane/fixtures/trendcoffee/order.json`
- Create: `tests/data_plane/fixtures/trendcoffee/payment.json`
- Test: `tests/data_plane/test_trendcoffee_adapter.py`
- Test: `tests/data_plane/test_trendcoffee_live.py`

**Interfaces:**
- Consumes: `StructuredSourceAdapter`, discovery evidence and normalized contracts.
- Produces: registered adapter key `trendcoffee`; operations `branch.list`, `menu.list`, `order.read`, quarantined `order.place`, quarantined `payment.initiate`, and normalized `branch`, `menu_item`, `order`, `payment` resources.

- [ ] **Step 1: Add minimal evidence-backed fixtures and failing normalization tests**

Use the public GET fields observed on 2026-08-20, including branch slug `ba9355f797`, product slug `23f99adf51`, variant slug `d5de540d4c`, size `tiêu chuẩn`, and price `35000`. Mark `branch.json` and `products.json` as captured public responses. Mark `order.json` and `payment.json` as schema examples reconstructed from first-party bundle contracts, not captured mutation responses; they cannot promote write trust. Replace those two examples with redacted authorized captures before enabling a live write fingerprint.

```python
def test_trend_adapter_normalizes_branch_product_and_variant(fixture_evidence):
    adapter = TrendCoffeeAdapter()
    capability = adapter.compile(fixture_evidence)
    menu = adapter.normalize("menu_item", load_fixture("products.json"))
    coffee = next(r for r in menu.records if r.resource_id == "23f99adf51")
    assert capability.operations["menu.list"].trust is TrustState.READ_VALIDATED
    assert capability.operations["order.place"].trust is TrustState.QUARANTINED
    assert coffee.payload["variants"][0] == {
        "slug": "d5de540d4c", "size": "tiêu chuẩn", "price": 35000,
    }
```

- [ ] **Step 2: Verify red**

Run: `uv run pytest tests/data_plane/test_trendcoffee_adapter.py -q`

Expected: FAIL because `TrendCoffeeAdapter` does not exist.

- [ ] **Step 3: Implement fingerprint, contracts, pagination and normalization**

The adapter matches `https://trendcoffee.net`, API base evidence ending in `/api/latest`, and response envelopes with `statusCode` plus `result`. It compiles:

```python
"branch.list": OperationContract("branch.list", "GET", "/branch", "branch", TrustState.READ_VALIDATED, True)
"menu.list": OperationContract("menu.list", "GET", "/products?page={page}&size={size}", "menu_item", TrustState.READ_VALIDATED, True)
"order.read": OperationContract("order.read", "GET", "/orders/{order_id}", "order", TrustState.READ_VALIDATED, True)
"order.place": OperationContract("order.place", "POST", "/orders/public", "order", TrustState.QUARANTINED, False, verify_operation="order.read")
"payment.initiate": OperationContract("payment.initiate", "POST", "/payment/initiate/public", "payment", TrustState.QUARANTINED, False, verify_operation="order.read")
```

`SourceCapability.origin` remains the publisher origin while
`SourceCapability.base_url` is the validated API base
`https://trendcoffee.net/api/latest`; `DirectExecutionEngine` joins operation
paths only against `base_url`.

Normalize payment fixtures from the provider envelope fields
`result.qrCode`, `result.slug`, `result.status`, and `result.order`. The request
builder accepts exactly `{"order": order_id, "paymentMethod": "bank-transfer"}`
for the initial QR flow.

Do not persist the current hashed bundle filename as a permanent endpoint. Persist its SHA-256 fingerprint and re-discover it from the HTML script tag.

- [ ] **Step 4: Add opt-in live read test**

```python
@pytest.mark.live
def test_live_trendcoffee_public_reads_are_normalizable():
    if os.environ.get("OPENJARVIS_LIVE_TREND_READ") != "1":
        pytest.skip("set OPENJARVIS_LIVE_TREND_READ=1")
    result = discover_and_sync_trend("https://trendcoffee.net")
    assert result.browser_actions == 0
    assert result.snapshot_count >= 2
```

The live test sends GET only and never creates an order/payment.

- [ ] **Step 5: Run fixture tests and optional live read**

Run: `uv run pytest tests/data_plane/test_trendcoffee_adapter.py -q`

Expected: PASS.

Optional run: `OPENJARVIS_LIVE_TREND_READ=1 uv run pytest tests/data_plane/test_trendcoffee_live.py -q -m live`

Expected: PASS when network is available; otherwise leave the explicit live gate skipped.

- [ ] **Step 6: Commit**

```bash
git add src/openjarvis/data_plane/adapters/trendcoffee.py tests/data_plane
git commit -m "feat(data-plane): discover Trend Coffee read contracts"
```

### Task 6: Implement DirectExecutionEngine sync, receipts, and verification

**Files:**
- Create: `src/openjarvis/data_plane/execution.py`
- Test: `tests/data_plane/test_execution.py`
- Test: `tests/data_plane/test_warm_path.py`

**Interfaces:**
- Consumes: capability/snapshot stores, adapter registry, `check_ssrf`, HTTPX, EventBus.
- Produces: `ExecutionTransport`, `HttpExecutionTransport`,
  `McpExecutionTransport`, `DirectExecutionEngine.sync(source_id, resources)`,
  `.execute(source_id, operation, arguments)`, `.verify(receipt_id)`, `.close()`.

- [ ] **Step 1: Write failing sync and no-browser warm-path tests**

```python
@respx.mock
def test_sync_uses_validated_contract_and_commits_snapshot(runtime):
    respx.get("https://trendcoffee.net/api/latest/branch").mock(
        return_value=httpx.Response(200, json=branch_fixture())
    )
    receipt = runtime.direct.sync("trend-coffee", ["branch"])
    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert runtime.snapshots.query(branch_query()).items[0].resource_id == "ba9355f797"


def test_warm_sync_never_invokes_browser(runtime):
    runtime.direct.sync("trend-coffee", ["menu_item"])
    runtime.browser.observe.assert_not_called()
```

- [ ] **Step 2: Write failing mutation retry and ambiguity tests**

```python
def test_post_timeout_is_ambiguous_and_not_retried(runtime, transport):
    transport.request.side_effect = httpx.ReadTimeout("after send")
    receipt = runtime.direct.execute("trend-coffee", "order.place", confirmed_args())
    assert receipt.status is ReceiptStatus.UNKNOWN
    assert transport.request.call_count == 1


def test_verify_is_a_separate_get(runtime, successful_order_receipt):
    result = runtime.direct.verify(successful_order_receipt.receipt_id)
    assert result.operation == "order.read"
    assert result.observed is True
```

Add parameterized transport tests for REST pagination, GraphQL POST execution
after discovery, existing MCP adapter reuse, persistent cookies, credential
reference resolution, CSRF header refresh, response-schema rejection,
`Retry-After`, redirect revalidation, and stale/revoked capability failure.
All use fake transports or `respx`; none reaches a live provider.

- [ ] **Step 3: Verify red**

Run: `uv run pytest tests/data_plane/test_execution.py tests/data_plane/test_warm_path.py -q`

Expected: FAIL on missing `execution.py`.

- [ ] **Step 4: Implement persistent per-origin clients and safe retry**

Keep `dict[str, httpx.Client]` keyed by origin. Validate capability trust, HTTPS, exact origin, method and formatted path before sending. Retry GET/HEAD only for connection failures, `429`, or `503`, bounded by two attempts and `Retry-After`; never retry POST unless `idempotency_header` is non-empty and a key was supplied. Resolve `SourceCapability.credential_ref` in `section:key` form through existing `get_tool_credential(section, key)` only at request time; never put the value in capability JSON, receipts, events, or errors. HTTPX owns the backend-only cookie jar. For CSRF, when an operation declares `csrf_cookie` and `csrf_header`, copy that cookie value into the request header immediately before dispatch and test expiry/missing-token failure.

Select transport from `SourceCapability.transport`. `HttpExecutionTransport`
uses the persistent HTTPX client. `McpExecutionTransport` receives an injected
mapping of existing `MCPToolAdapter` instances and calls the declared adapter;
the outer semantic Data Plane tool has already entered through `ToolExecutor`,
so MCP does not create another orchestration or policy path. Add a fake-MCP
test proving a read result is normalized and committed through the same store.

Persist receipts in `execution_receipts` within the structured database:

```sql
CREATE TABLE IF NOT EXISTS execution_receipts (
    receipt_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    capability_revision INTEGER NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_ref TEXT NOT NULL DEFAULT '',
    normalized_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
```

An unsafe response is normalized into receipt-owned candidate data but does
not replace an authoritative snapshot. `verify()` executes the declared safe
observation, compares merchant/order/payment references with the request hash,
and only then commits the normalized candidate plus observed state. For Trend
Coffee payment initiation, this is how the provider QR from the receipt becomes
a verified `payment` snapshot after `order.read` confirms the associated order;
a mismatch returns `VerificationFailed` and never publishes the QR artifact.

- [ ] **Step 5: Publish redacted lifecycle events**

Publish `SOURCE_SYNC_STARTED`, `SOURCE_SYNC_COMMITTED`, `SOURCE_SYNC_FAILED`, `SOURCE_EXECUTE_STARTED`, `SOURCE_EXECUTE_RECEIPT_CREATED`, and `SOURCE_VERIFY_COMPLETED` with source id, operation, capability revision, timing, count and receipt status only. Never publish headers, cookies, request body, payment payload, or full provider response.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/data_plane/test_execution.py tests/data_plane/test_warm_path.py -q`

Expected: PASS.

```bash
git add src/openjarvis/data_plane/execution.py tests/data_plane/test_execution.py tests/data_plane/test_warm_path.py
git commit -m "feat(data-plane): add direct structured execution"
```

### Task 7: Expose semantic tools and compose the Data Plane in OpenJarvis

**Files:**
- Create: `src/openjarvis/tools/data_plane.py`
- Modify: `src/openjarvis/core/config.py:1591-1640,1898-1928`
- Modify: `src/openjarvis/system/bundles.py:1-72`
- Modify: `src/openjarvis/system/core.py:53-90,302-338`
- Modify: `src/openjarvis/system/builder.py:148-371,467-579`
- Modify: `src/openjarvis/security/capabilities.py`
- Test: `tests/tools/test_data_plane_tools.py`
- Test: `tests/system/test_data_plane_wiring.py`
- Test: `tests/core/test_config.py`
- Modify: `tests/security/test_capabilities.py`

**Interfaces:**
- Consumes: Tasks 1--6 engines/stores.
- Produces: native tools `source_discover`, `source_sync`, `structured_query`, `source_execute`, `source_verify`; `DataPlaneRuntime` composition bundle; `[data_plane]` config.

- [ ] **Step 1: Write failing tool metadata tests**

```python
def test_data_plane_tool_capabilities_and_kinds():
    assert SourceDiscoverTool().spec.required_capabilities == ["source:discover"]
    assert SourceSyncTool().spec.metadata == {"observes": True}
    assert StructuredQueryTool().spec.metadata == {"observes": True}
    assert SourceExecuteTool().spec.metadata == {"mutates": True}
    assert SourceVerifyTool().spec.metadata == {"observes": True}
```

Add `SOURCE_DISCOVER`, `SOURCE_READ`, `SOURCE_WRITE`, and `ARTIFACT_PUBLISH`
to the existing `Capability` enum with the exact wire values used above. Pin
default-open and explicit-deny behavior in `tests/security/test_capabilities.py`;
do not create a second policy engine.

Each tool result must be compact JSON built from receipt/result `to_dict()`, not raw HTTP payloads.

- [ ] **Step 2: Write failing builder ownership/injection tests**

```python
def test_builder_creates_one_shared_data_plane(tmp_path, config, engine):
    config.data_plane.enabled = True
    config.data_plane.db_path = str(tmp_path / "structured.db")
    config.tools.enabled = ["source_discover", "source_sync", "structured_query"]
    system = SystemBuilder(config).engine_instance(engine).build()
    assert system.data_plane.discovery.capability_store is system.data_plane.capabilities
    assert all(tool._runtime is system.data_plane for tool in system.tools)
```

- [ ] **Step 3: Verify red**

Run: `uv run pytest tests/tools/test_data_plane_tools.py tests/system/test_data_plane_wiring.py tests/core/test_config.py -q`

Expected: FAIL on missing config/runtime/tools.

- [ ] **Step 4: Add config and composition bundle**

```python
@dataclass(slots=True)
class DataPlaneConfig:
    enabled: bool = False
    db_path: str = ""
    artifact_dir: str = ""
    discovery_budget_seconds: int = 60
    browser_fallback: bool = False
    source_url: str = ""
    trusted_write_operations: str = ""


@dataclass
class DataPlaneRuntime:
    capabilities: SQLiteCapabilityStore
    snapshots: StructuredSnapshotStore
    discovery: DiscoveryEngine
    direct: DirectExecutionEngine
```

Add `data_plane` to `JarvisConfig` and `top_sections`. Resolve empty paths under `get_data_dir()` as `structured.db` and `artifacts/`.

- [ ] **Step 5: Construct once and inject by category**

In `_build()`, create the runtime before `_resolve_tools()`, pass it into tool resolution, assign tools whose category is `data_plane`, and append the `data_plane` field after all existing positional `JarvisSystem` fields. `JarvisSystem.close()` closes direct HTTP clients and SQLite stores once. After external MCP discovery completes, call `data_plane.direct.set_mcp_tools(self._mcp_tools)` before returning the system; this reuses the existing MCP client/adapter pool.

- [ ] **Step 6: Implement semantic tools**

```python
@ToolRegistry.register("source_sync")
class SourceSyncTool(_DataPlaneTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="source_sync", description="Synchronize validated structured resources.",
            parameters={"type": "object", "properties": {
                "source_id": {"type": "string"},
                "resources": {"type": "array", "items": {"type": "string"}},
            }, "required": ["source_id", "resources"]},
            category="data_plane", required_capabilities=["source:read"],
            metadata={"observes": True}, timeout_seconds=35.0,
        )
```

`structured_query` implements `cached`, `refresh_if_stale`, and `live`; only `live` synchronously calls `DirectExecutionEngine.sync` first.

`SourceDiscoverTool.execute` calls `DiscoveryEngine.discover`; when a new
read-validated capability is returned, it immediately calls
`DirectExecutionEngine.sync` for its declared read resources and includes the
`SyncReceipt` in the tool result. A cache hit does not trigger static or browser
discovery.

At this read-only milestone, `SourceExecuteTool` is registered but rejects all
unsafe contracts with `CapabilityQuarantined`; Task 9 is the only task that
enables its write path. Explicitly import the generic and Trend Coffee adapter
modules during Data Plane composition, then assert the registry contains both
keys so registry clearing/reload cannot silently produce zero adapters.

- [ ] **Step 7: Run focused suite and commit**

Run: `uv run pytest tests/tools/test_data_plane_tools.py tests/system/test_data_plane_wiring.py tests/core/test_config.py -q`

Expected: PASS.

```bash
git add src/openjarvis/tools/data_plane.py src/openjarvis/core/config.py src/openjarvis/security/capabilities.py src/openjarvis/system tests/tools/test_data_plane_tools.py tests/system/test_data_plane_wiring.py tests/core/test_config.py tests/security/test_capabilities.py
git commit -m "feat(data-plane): wire semantic source tools"
```

### Task 8: Replace production FakeMerchant with TrendCoffeeMerchant

**Files:**
- Create: `src/openjarvis/merchants/trendcoffee.py`
- Modify: `src/openjarvis/merchants/__init__.py`
- Modify: `src/openjarvis/core/config.py:1591-1601`
- Modify: `src/openjarvis/system/builder.py:467-579`
- Modify: `configs/openjarvis/examples/ordering-kiosk.toml`
- Test: `tests/merchants/test_trendcoffee_merchant.py`
- Modify: `tests/system/test_ordering_wiring.py`
- Modify: `tests/merchants/test_fake_merchant.py`

**Interfaces:**
- Consumes: existing `MerchantPort`, `DirectExecutionEngine`, snapshot query, Trend adapter contracts.
- Produces: `TrendCoffeeMerchant(runtime, source_id="trend-coffee")` implementing all eight `MerchantPort` methods; production config `merchants.backend="trendcoffee"` and default `none`.

- [ ] **Step 1: Write failing real-merchant mapping/cart tests**

```python
def test_reads_branch_and_menu_from_structured_snapshot(runtime):
    merchant = TrendCoffeeMerchant(runtime)
    assert merchant.list_branches()[0].slug == "ba9355f797"
    coffee = merchant.search_menu("cà phê đen", "ba9355f797")[0]
    assert coffee.variants[0].slug == "d5de540d4c"


def test_cart_is_local_but_variant_is_validated_against_snapshot(runtime):
    merchant = TrendCoffeeMerchant(runtime)
    line_id = merchant.add_to_cart("d5de540d4c", 2, "mang đi")
    cart = merchant.read_cart()
    assert cart.lines[0].line_id == line_id
    assert cart.lines[0].line_total == 70_000
    with pytest.raises(ValueError, match="variant_unavailable"):
        merchant.add_to_cart("invented", 1, "")
```

- [ ] **Step 2: Verify red**

Run: `uv run pytest tests/merchants/test_trendcoffee_merchant.py tests/system/test_ordering_wiring.py -q`

Expected: FAIL because the real merchant is absent.

- [ ] **Step 3: Implement the facade and exact order payload**

Protect the process-local draft cart with `threading.RLock`. This retains Phase 1's one-customer-at-a-time constraint without inventing Kiosk-owned session state.

Map `place_order("take-out", branch)` to:

```python
{
    "type": "take-out",
    "timeLeftTakeOut": 0,
    "deliveryTo": "",
    "deliveryPhone": "",
    "table": "",
    "branch": branch_slug,
    "owner": "",
    "approvalBy": "",
    "orderItems": [
        {"quantity": line.quantity, "variant": line.variant_slug,
         "promotion": None, "note": line.note}
        for line in cart.lines
    ],
    "voucher": None,
    "description": "",
}
```

This payload is derived from Trend Coffee's first-party bundle observed on 2026-08-20. Keep it isolated inside `TrendCoffeeAdapter.build_request("order.place", args)` so fingerprint/schema demotion can block it.

Until Task 9 lands, `TrendCoffeeMerchant.place_order` fails locally with
`CapabilityQuarantined` before `DirectExecutionEngine`; branch/menu/cart remain
usable and no production ordering call can bypass approval during intermediate
commits.

- [ ] **Step 4: Change production wiring, retain FakeMerchant only in tests**

Set `MerchantsConfig.backend = "none"` and add `MerchantsConfig.source_id = "trend-coffee"`. Builder constructs `TrendCoffeeMerchant(runtime, source_id=config.merchants.source_id)` only for `backend == "trendcoffee"` and an enabled Data Plane; `backend == "fake"` remains accepted solely for existing deterministic tests. Update `ordering-kiosk.toml` to `backend = "trendcoffee"` and `source_id = "trend-coffee"`.

- [ ] **Step 5: Run ordering contracts and commit**

Run: `uv run pytest tests/merchants tests/tools/test_ordering_cart.py tests/tools/test_ordering_menu.py tests/tools/test_ordering_order.py tests/tools/test_ordering_doctrine.py tests/system/test_ordering_wiring.py -q`

Expected: PASS; doctrine still enumerates exactly the original eight tools.

```bash
git add src/openjarvis/merchants src/openjarvis/core/config.py src/openjarvis/system/builder.py configs/openjarvis/examples/ordering-kiosk.toml tests/merchants tests/system/test_ordering_wiring.py
git commit -m "feat(merchants): use Trend Coffee in production ordering"
```

### Task 9: Enforce one-time write trust and exact-request approval

**Files:**
- Create: `src/openjarvis/data_plane/approval.py`
- Modify: `src/openjarvis/data_plane/adapters/trendcoffee.py`
- Modify: `src/openjarvis/data_plane/execution.py`
- Modify: `src/openjarvis/tools/data_plane.py`
- Modify: `src/openjarvis/tools/approval_store.py`
- Modify: `src/openjarvis/core/config.py`
- Modify: `src/openjarvis/merchants/port.py`
- Modify: `src/openjarvis/merchants/fake.py`
- Modify: `src/openjarvis/merchants/trendcoffee.py`
- Modify: `src/openjarvis/tools/ordering.py`
- Test: `tests/data_plane/test_write_trust.py`
- Test: `tests/tools/test_source_execute_approval.py`
- Modify: `tests/tools/test_ordering_order.py`
- Create: `tests/tools/test_approval_store.py`

**Interfaces:**
- Consumes: `ApprovalStore`, `SourceCapability`, configured `trusted_write_operations`.
- Produces: `ExecutionApprovalGate.prepare(source_id, operation, arguments, capability_revision) -> PendingAction`, `.authorize(approval_id, expected_hash) -> ExecutionGrant`, operator-trusted promotion, and `source_execute(source_id, operation, arguments, approval_id="")` pending/resume behavior.

- [ ] **Step 1: Write failing trust and approval tests**

```python
def test_discovery_never_auto_promotes_write(discovered_trend_capability):
    assert discovered_trend_capability.operations["order.place"].trust is TrustState.QUARANTINED


def test_operator_trust_requires_matching_provider_operation_and_fingerprint(store, cap):
    promoted = promote_trusted_writes(
        cap, "trendcoffee:order.place:sha256:expected"
    )
    assert promoted.operations["order.place"].trust is TrustState.WRITE_VALIDATED
    changed = replace(cap, fingerprint="sha256:changed")
    assert promote_trusted_writes(changed, "trendcoffee:order.place:sha256:expected").operations["order.place"].trust is TrustState.QUARANTINED


def test_source_execute_queues_then_consumes_exact_approval(tool, store):
    pending = tool.execute(source_id="trend-coffee", operation="order.place", arguments=order_args())
    approval_id = pending.metadata["approval_id"]
    assert pending.metadata["pending_approval"] is True
    store.update_status(approval_id, STATUS_APPROVED)
    done = tool.execute(source_id="trend-coffee", operation="order.place", arguments=order_args(), approval_id=approval_id)
    assert done.success is True


def test_only_one_concurrent_claim_can_enter_execution(store, approved_action):
    results = claim_from_two_threads(store, approved_action.id)
    assert sorted(result.status for result in results) == ["claimed", "rejected"]
```

Also change one argument after approval and assert `approval_request_hash_mismatch` with no provider request.

- [ ] **Step 2: Verify red**

Run: `uv run pytest tests/data_plane/test_write_trust.py tests/tools/test_source_execute_approval.py tests/tools/test_approval_store.py -q`

Expected: FAIL on missing approval gate.

- [ ] **Step 3: Implement canonical request hashes and ApprovalStore reuse**

```python
def execution_request_hash(source_id, operation, arguments, capability_revision):
    payload = json.dumps({
        "source_id": source_id, "operation": operation,
        "arguments": arguments, "capability_revision": capability_revision,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
```

Queue a high-tier `PendingAction` whose payload contains only source, operation, redacted preview, capability revision and request hash. Extend the existing `ApprovalStore` with `STATUS_EXECUTING` and one locked `BEGIN IMMEDIATE` compare-and-set method that validates approved status, expiry, action type and request hash, then changes `approved -> executing`. `authorize` uses that method and returns an in-memory `ExecutionGrant` containing approval id, request hash, and a single-use nonce; the grant has no serializer and is never persisted or exposed as tool output. Change `DirectExecutionEngine.execute(source_id, operation, arguments, grant=None)` in this task to require that grant for every unsafe operation and validate it immediately before dispatch. Receipt creation transitions `executing -> executed`; a timeout-after-send transitions it to `unknown` and never makes it reusable. A safe operation rejects a supplied grant, and an unsafe direct engine call without one fails before transport invocation.

Extend `MerchantPort.place_order` compatibly to
`place_order(order_type, branch_slug, approval_id="")`. `FakeMerchant` accepts
and ignores the optional id so deterministic tests remain simple.
`TrendCoffeeMerchant.place_order` prepares the exact provider arguments and
uses the same `ExecutionApprovalGate`; when approval is absent it raises
`OrderApprovalRequired(approval_id, request_hash)`. Add optional `approval_id`
to `OrderPlaceTool`'s schema, catch that exception, and return a failed
`ToolResult` with `metadata={"pending_approval": True,
"approval_id": exc.approval_id, "request_hash": exc.request_hash}`. An approved second `order_place` call executes exactly
once through `DirectExecutionEngine`. This prevents the legacy ordering tool
from bypassing the `source_execute` approval gate.

- [ ] **Step 4: Promote writes only from explicit operator trust config**

Parse entries as `provider:operation:fingerprint`. Do not accept wildcards. Re-evaluate on discovery refresh; a fingerprint/schema/origin change demotes the operation before any network call.

For `payment.initiate`, accept only `order` and
`paymentMethod="bank-transfer"`, require its own approval hash after order
verification, and normalize the merchant-returned QR fields. The QR string may
enter the payment snapshot/artifact only from that normalized response.

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/data_plane/test_write_trust.py tests/tools/test_source_execute_approval.py tests/tools/test_approval_store.py tests/server/test_approval_routes.py -q`

Expected: PASS.

```bash
git add src/openjarvis/data_plane src/openjarvis/tools/data_plane.py src/openjarvis/tools/approval_store.py src/openjarvis/core/config.py src/openjarvis/merchants/port.py src/openjarvis/merchants/fake.py src/openjarvis/merchants/trendcoffee.py src/openjarvis/tools/ordering.py tests/data_plane tests/tools/test_source_execute_approval.py tests/tools/test_ordering_order.py tests/tools/test_approval_store.py
git commit -m "feat(data-plane): gate validated provider mutations"
```

### Task 10: Add passive Playwright network observation as bounded fallback

**Files:**
- Modify: `src/openjarvis/tools/browser.py:12-48`
- Create: `src/openjarvis/data_plane/browser_observer.py`
- Modify: `src/openjarvis/data_plane/execution.py`
- Modify: `src/openjarvis/system/builder.py`
- Test: `tests/data_plane/test_browser_observer.py`
- Modify: `tests/data_plane/test_execution.py`
- Modify: `tests/integration/test_browser_capability.py`

**Interfaces:**
- Consumes: `BrowserObservationPort` from Task 4 and existing `_BrowserSession` as the sole in-process Playwright owner.
- Produces: `_BrowserSession.observe_network(url, timeout_ms, max_bodies)`, `PlaywrightBrowserObserver.observe(origin, timeout_seconds, credential_ref="")` returning sanitized `DiscoveryEvidence`, plus an in-memory authenticated-session handoff to `DirectExecutionEngine`.

- [ ] **Step 1: Write failing passive-observation test**

```python
def test_observer_collects_same_origin_json_without_click_or_type(fake_page):
    observer = PlaywrightBrowserObserver(session=fake_session(fake_page))
    evidence = observer.observe("https://shop.test", timeout_seconds=2)
    assert [item.source_url for item in evidence] == ["https://shop.test/api/menu"]
    assert all("cookie" not in json.dumps(item.payload).lower() for item in evidence)
    fake_page.goto.assert_called_once()
    assert not fake_page.click.called
    assert not fake_page.type.called


def test_authenticated_handoff_never_persists_cookie(observer, direct, stores):
    observer.observe(
        "https://shop.test", timeout_seconds=2,
        credential_ref="browser_sessions:shop-test",
    )
    direct.adopt_browser_session.assert_called_once()
    assert "cookie" not in stores.persisted_json().lower()
```

- [ ] **Step 2: Verify red**

Run: `uv run pytest tests/data_plane/test_browser_observer.py -q`

Expected: FAIL on missing observer.

- [ ] **Step 3: Extend the existing browser session, not the MCP Agent path**

Register Playwright `response` listeners before `page.goto`; accept only Fetch/XHR responses whose URL passes SSRF and whose content type is JSON. Capture URL, method, status, selected non-secret headers, schema sample and body hash; cap body count and bytes. Detach listeners in `finally`.

If no structured network response is observed, return one bounded `dom_html`
evidence record from `page.content()` for the existing generic JSON-LD/embedded
JSON parser. This is the final DOM fallback; it remains data, never an
instruction, and performs no click/type.

For a provider adapter that declares browser-assisted authentication, resolve
an opaque Playwright storage-state reference through the existing credential
backend, open only its declared login/navigation URL, and wait for the adapter's
authenticated URL/cookie predicate. Never auto-fill unknown forms. On success,
pass same-origin cookies directly in memory to
`DirectExecutionEngine.adopt_browser_session`; never place cookies/tokens in
`DiscoveryEvidence`, `SourceCapability`, snapshots, events, traces, or tool
output. If user interaction is required, return `AuthenticationRequired`
instead of improvising click/type actions.

Do not register a new browser tool and do not import `playwright.sync_api` in a new file. Update the invariant allowlist so `src/openjarvis/tools/browser.py` remains the only production in-process importer.

- [ ] **Step 4: Wire only when configured and prove cache bypass**

Builder imports/constructs `PlaywrightBrowserObserver` only when `data_plane.browser_fallback=true`. Discovery calls it after static/safe HTTP stages and with at most 20 seconds remaining. Add a test showing a cached validated capability returns before observer construction/call.

- [ ] **Step 5: Run browser-focused tests and commit**

Run: `uv run pytest tests/data_plane/test_browser_observer.py tests/integration/test_browser_capability.py -q -m "not integration"`

Expected: PASS without launching a browser.

```bash
git add src/openjarvis/tools/browser.py src/openjarvis/data_plane/browser_observer.py src/openjarvis/data_plane/execution.py src/openjarvis/system/builder.py tests/data_plane/test_browser_observer.py tests/data_plane/test_execution.py tests/integration/test_browser_capability.py
git commit -m "feat(data-plane): add passive browser discovery fallback"
```

### Task 11: Build immutable ArtifactWorkspace and `artifact_render`

**Files:**
- Create: `src/openjarvis/artifacts/__init__.py`
- Create: `src/openjarvis/artifacts/types.py`
- Create: `src/openjarvis/artifacts/workspace.py`
- Create: `src/openjarvis/tools/artifact_render.py`
- Modify: `src/openjarvis/system/bundles.py`
- Modify: `src/openjarvis/system/builder.py`
- Test: `tests/artifacts/test_workspace.py`
- Test: `tests/tools/test_artifact_render.py`

**Interfaces:**
- Consumes: structured snapshot scopes, EventBus, `ToolExecutor` capability policy.
- Produces: `ArtifactBundle`, `ArtifactManifest`, `ArtifactRef`, `ArtifactWorkspace.publish/get/activate/rollback`, and `artifact_render` publishing a candidate revision.

- [ ] **Step 1: Write failing immutable revision/rollback tests**

```python
def test_publish_is_immutable_and_failed_revision_keeps_active(tmp_path, bus):
    workspace = ArtifactWorkspace(tmp_path, bus=bus)
    first = workspace.publish(valid_bundle("one"), valid_manifest())
    workspace.activate(first)
    with pytest.raises(DataPlaneError) as exc:
        workspace.publish(bundle_with_external_script(), valid_manifest(), first.revision)
    assert exc.value.code is DataPlaneErrorCode.ARTIFACT_REJECTED
    assert workspace.active(first.artifact_id) == first


def test_rollback_reactivates_previous_revision(workspace):
    first = workspace.publish(valid_bundle("one"), valid_manifest())
    second = workspace.publish(valid_bundle("two"), valid_manifest(), first.revision)
    workspace.activate(second)
    assert workspace.rollback(first.artifact_id).revision == first.revision
```

- [ ] **Step 2: Write failing artifact tool test**

```python
def test_artifact_render_publishes_complete_agent_authored_bundle(tool):
    result = tool.execute(
        title="Order QR", html="<main id='app'></main>",
        css="body{margin:0}", js="document.querySelector('#app').textContent='Ready'",
        snapshot_scopes=["trend-coffee:payment"], allowed_intents=["snapshot.query"],
    )
    payload = json.loads(result.content)
    assert payload["url"].startswith("/v1/artifacts/")
    assert payload["state"] == "candidate"
```

- [ ] **Step 3: Verify red**

Run: `uv run pytest tests/artifacts/test_workspace.py tests/tools/test_artifact_render.py -q`

Expected: FAIL on missing artifact package/tool.

- [ ] **Step 4: Implement validation, immutable storage and activation events**

Accept only `index.html`, `styles.css`, and `app.js`. Permit HTML references only to the platform-served `./bridge.js`, `./styles.css`, and `./app.js`; reject remote/inline `script` and `style`, `iframe`, `object`, `base`, and `form`. Reject generated JS `eval`, `new Function`, `fetch`, XHR, WebSocket, EventSource, sendBeacon and dynamic import; reject CSS `@import` and remote `url()`. CSP remains the runtime enforcement layer. The fixed `bridge.js` defines a typed `window.openJarvis` API over the transferred `MessagePort`; it is platform code, not Agent-generated code.

Store under `<artifact_dir>/<artifact_id>/<revision>/` where revision is SHA-256 of canonical manifest and bundle content. Use write-to-new-directory then SQLite manifest insert; never modify an existing revision.

- [ ] **Step 5: Inject workspace and register the tool**

Append `artifacts: ArtifactWorkspace | None` to `DataPlaneRuntime`; assign the workspace to tools with category `artifact`. `artifact_render` requires `artifact:publish`, validates and publishes an immutable candidate, emits `ARTIFACT_PUBLISHED`, and returns its URL/nonce/revision. It does not activate the candidate. Runtime readiness and activation occur through the Kiosk staging handshake in Tasks 12--13, so a broken new revision cannot replace the current active artifact.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/artifacts/test_workspace.py tests/tools/test_artifact_render.py -q`

Expected: PASS.

```bash
git add src/openjarvis/artifacts src/openjarvis/tools/artifact_render.py src/openjarvis/system tests/artifacts tests/tools/test_artifact_render.py
git commit -m "feat(artifacts): add sandboxed artifact workspace"
```

### Task 12: Serve artifacts and route typed UI intents through Agent authority

**Files:**
- Create: `src/openjarvis/artifacts/routes.py`
- Modify: `src/openjarvis/server/app.py:228-630`
- Modify: `src/openjarvis/cli/serve.py:483-507`
- Modify: `src/openjarvis/server/ws_bridge.py:14-38`
- Test: `tests/server/test_artifact_routes.py`
- Modify: `tests/server/test_app_wiring.py`
- Modify: `tests/server/test_ws_bridge.py`

**Interfaces:**
- Consumes: `ArtifactWorkspace`, `StructuredSnapshotStore`, `NativeAgentRuntime`, FastAPI auth.
- Produces: `GET /v1/artifacts/active`, immutable revision assets plus fixed `bridge.js`, `POST /v1/artifacts/{id}/revisions/{revision}/ready`, `POST /v1/artifacts/{id}/actions`, and publish/activation WebSocket events.

- [ ] **Step 1: Write failing CSP and immutable route tests**

```python
def test_artifact_asset_has_strict_csp_and_no_cache(client, active_artifact):
    response = client.get(active_artifact.url + "/index.html")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == EXPECTED_ARTIFACT_CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
```

- [ ] **Step 2: Write failing read/action authority tests**

```python
def test_declared_snapshot_query_bypasses_agent(client, runtime, manifest):
    response = client.post(action_url(manifest), json=envelope("snapshot.query", {"resource_type": "menu_item"}))
    assert response.status_code == 200
    runtime.native_agent.run.assert_not_called()


def test_business_intent_becomes_ui_action_for_agent(client, runtime, manifest):
    response = client.post(action_url(manifest), json=envelope("order.confirm", {"order_id": "o1"}))
    assert response.status_code == 200
    prompt = runtime.native_agent.run.call_args.args[0]
    assert '"type":"ui_action"' in prompt
    assert "source_execute" not in json.loads(prompt)["intent"]


def test_ready_activates_exact_candidate_and_bad_nonce_keeps_previous(client, workspace):
    previous, candidate = active_and_candidate(workspace)
    denied = client.post(ready_url(candidate), json={"nonce": "wrong"})
    assert denied.status_code == 403
    assert workspace.active(previous.artifact_id) == previous
    accepted = client.post(ready_url(candidate), json={"nonce": candidate.nonce})
    assert accepted.status_code == 204
    assert workspace.active(candidate.artifact_id) == candidate
```

- [ ] **Step 3: Verify red**

Run: `uv run pytest tests/server/test_artifact_routes.py tests/server/test_app_wiring.py tests/server/test_ws_bridge.py -q`

Expected: FAIL because artifact routes are not mounted.

- [ ] **Step 4: Implement immutable asset/read/action routes**

Validate `artifact_id`, active revision, nonce, manifest allowlist, JSON schema, 64 KiB payload limit, and per-artifact request rate. `snapshot.query` directly calls the store only for declared source/resource scopes. All other allowed semantic intents create:

```json
{"type":"ui_action","artifact_id":"a1","revision":"r2","intent":"order.confirm","payload":{}}
```

Serialize that object as `prompt`, then run it through `app.state.native_agent_runtime.bind(model=app.state.model).run(prompt, AgentContext())`. Never accept raw tool names as intent.

Serve a fixed, versioned `bridge.js` beside each bundle without writing it into
the immutable Agent bundle. The readiness route accepts only the current
candidate revision and matching one-time nonce, calls
`ArtifactWorkspace.activate`, and emits `ARTIFACT_ACTIVATED`. Action routes
accept only the active revision. A readiness timeout, rejected nonce, or
candidate load error leaves the previous revision active and emits
`ARTIFACT_REJECTED`; explicit rollback still uses the workspace API.

- [ ] **Step 5: Wire serving lifecycle and WebSocket event**

Pass `system.data_plane` from `serve.py` into `create_app`, put it on `app.state`, mount the router before the SPA catch-all, close it through `JarvisSystem.close`, and add `ARTIFACT_PUBLISHED`, `ARTIFACT_ACTIVATED`, `ARTIFACT_REJECTED`, and `ARTIFACT_ROLLED_BACK` to `_AGENT_EVENTS`.

- [ ] **Step 6: Run server tests and commit**

Run: `uv run pytest tests/server/test_artifact_routes.py tests/server/test_app_wiring.py tests/server/test_ws_bridge.py tests/server/test_auth_middleware.py -q`

Expected: PASS.

```bash
git add src/openjarvis/artifacts/routes.py src/openjarvis/server/app.py src/openjarvis/server/ws_bridge.py src/openjarvis/cli/serve.py tests/server
git commit -m "feat(server): serve artifacts and route UI intents"
```

### Task 13: Replace the fixed Kiosk display with the sandboxed artifact host

**Files:**
- Create: `frontend/src/hooks/useArtifactHost.ts`
- Create: `frontend/src/hooks/useArtifactHost.test.ts`
- Modify: `frontend/src/pages/KioskPage.tsx:45-144`
- Modify: `frontend/src/lib/viteProxy.test.ts`
- Modify: `frontend/vite.config.ts:55-78`
- Delete: `frontend/public/display.html`
- Delete: `tests/server/test_display_page.py`
- Create: `tests/server/test_kiosk_artifact_host.py`

**Interfaces:**
- Consumes: activation WebSocket event, immutable artifact URL, typed action endpoint.
- Produces: `useArtifactHost()` returning active/candidate iframe refs, URLs, status and load handlers; Kiosk iframe pair with `sandbox="allow-scripts"` and no fixed display renderer.

- [ ] **Step 1: Write failing hook tests for activation and bridge handoff**

```typescript
it('stages published revision and shows it only after activation', async () => {
  const host = renderArtifactHost();
  socket.emit({type: 'artifact.published', data: {
    artifact_id: 'a1', revision: 'r2', url: '/v1/artifacts/a1/revisions/r2', nonce: 'n1'
  }});
  expect(host.result.current.candidateUrl).toBe('/v1/artifacts/a1/revisions/r2/index.html');
  expect(host.result.current.activeUrl).not.toBe(host.result.current.candidateUrl);
  host.candidateFrame.dispatchEvent(new Event('load'));
  host.candidatePort.emit({type: 'artifact.ready', artifact_id: 'a1', revision: 'r2', nonce: 'n1'});
  expect(host.readyRequests).toHaveLength(1);
  socket.emit({type: 'artifact.activated', data: {artifact_id: 'a1', revision: 'r2'}});
  expect(host.result.current.activeUrl).toBe('/v1/artifacts/a1/revisions/r2/index.html');
});

it('rejects a window message not sent by the hosted iframe', () => {
  const host = renderArtifactHost();
  window.dispatchEvent(new MessageEvent('message', {origin: 'null', source: window, data: {type: 'artifact.ready'}}));
  expect(host.transferredPorts).toHaveLength(0);
});
```

- [ ] **Step 2: Write failing source-level sandbox test**

```python
def test_kiosk_iframe_is_script_only_sandbox():
    source = Path("frontend/src/pages/KioskPage.tsx").read_text()
    assert 'sandbox="allow-scripts"' in source
    for forbidden in ("allow-same-origin", "allow-forms", "allow-popups", "/display.html"):
        assert forbidden not in source
```

- [ ] **Step 3: Verify red**

Run: `npm test -- --run src/hooks/useArtifactHost.test.ts` from `frontend/`.

Expected: FAIL because the hook does not exist.

Run: `uv run pytest tests/server/test_kiosk_artifact_host.py -q`

Expected: FAIL because Kiosk still references `/display.html`.

- [ ] **Step 4: Implement the parent-owned MessageChannel**

`useArtifactHost` opens the existing `/v1/agents/events` WebSocket and fetches `/v1/artifacts/active` on connection. `artifact.published` loads an invisible candidate iframe while the active iframe remains visible. On candidate load, require `event.source === candidateIframe.contentWindow` and `event.origin === "null"`, compare artifact/revision/nonce, then transfer one `MessagePort`. The fixed bridge responds `artifact.ready`; the parent POSTs the exact readiness nonce. Only `artifact.activated` atomically swaps the active/candidate iframe roles and CSS visibility without reloading the validated candidate. A timeout/load failure discards the candidate and retains the active iframe. After transfer, accept messages only on that port and POST typed envelopes to the announced action URL.

Cap bridge payloads at 64 KiB and 20 requests/second client-side; backend remains authoritative.

- [ ] **Step 5: Replace only the display layer in `KioskPage`**

Preserve `useKioskState`, `kioskVoiceCommand`, `usePipecatVoiceMode`, captions, Voice status, Visualizer and exit behavior byte-for-byte except for required hook imports/layout wiring. Render the active iframe normally and a candidate iframe in a hidden staging container; both use the same sandbox:

```tsx
<iframe
  ref={artifact.activeIframeRef}
  src={artifact.activeUrl ?? 'about:blank'}
  sandbox="allow-scripts"
  title="Jarvis artifact"
  className="absolute inset-0 h-full w-full border-0"
/>
```

The hidden candidate uses `candidateIframeRef`, `candidateUrl`, and
`onCandidateLoad`; it is removed after activation or rejection. There is never
more than one active business-action port.

Delete the old fixed `display.html`; there must be no second renderer or iframe-owned WebSocket.

- [ ] **Step 6: Proxy and test artifact routes**

The existing `/v1` HTTP+WS proxy already covers artifact endpoints. Add a test pinning `ws: true` and that artifact URLs use the same proxy target; do not add another Vite server.

- [ ] **Step 7: Run frontend/backend tests and build**

Run from `frontend/`: `npm test -- --run src/hooks/useArtifactHost.test.ts src/lib/viteProxy.test.ts`

Expected: PASS.

Run: `npm run build`

Expected: PASS and produce the server static bundle.

Run from repo root: `uv run pytest tests/server/test_kiosk_artifact_host.py tests/server/test_pwa_serving.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/hooks/useArtifactHost.ts frontend/src/hooks/useArtifactHost.test.ts frontend/src/pages/KioskPage.tsx frontend/src/lib/viteProxy.test.ts frontend/vite.config.ts frontend/public/display.html tests/server/test_display_page.py tests/server/test_kiosk_artifact_host.py
git commit -m "feat(kiosk): host generated sandboxed artifacts"
```

### Task 14: Update policy, preset, scheduler guard, and full acceptance harness

**Files:**
- Modify: `skills/serve-trend-coffee-customers/SKILL.md`
- Modify: `configs/openjarvis/prompts/ordering-kiosk.md`
- Modify: `configs/openjarvis/examples/ordering-kiosk.toml`
- Modify: `src/openjarvis/scheduler/scheduler.py`
- Create: `tests/integration/test_universal_data_plane.py`
- Create: `tests/integration/test_artifact_ordering_loop.py`
- Create: `tests/integration/test_data_plane_scheduler_policy.py`
- Create: `scripts/e2e_universal_data_plane.py`
- Create: `docs/testing/universal-data-plane-live.md`

**Interfaces:**
- Consumes: every prior task.
- Produces: direct-first Skill/prompt, real Kiosk preset, hermetic cold/warm/order/artifact tests, opt-in live harness and exact manual checklist.

- [ ] **Step 1: Write failing whole-path integration tests**

In these tests `call_tool(system, name, **arguments)` must build a `ToolCall`
and invoke `system.tool_executor.execute(tool_call)`; the executor already owns
the Agent identity/policy context. It must not call a tool object's `.execute()`
method directly. This pins the authority path.

```python
def test_cold_then_warm_path_persists_and_skips_browser(tmp_path, provider):
    first = build_system(tmp_path, provider, browser_fallback=True)
    cold = call_tool(first, "source_discover", source_ref=provider.origin)
    assert cold["capability"]["provider"] == "trendcoffee"
    first.close()

    second = build_system(tmp_path, provider, browser_fallback=True)
    warm = call_tool(second, "source_sync", source_id="trend-coffee", resources=["branch", "menu_item"])
    assert warm["browser_actions"] == 0
    assert provider.browser_calls == 0


def test_payment_artifact_uses_only_normalized_provider_qr(runtime):
    arguments = {"order": "order-1", "paymentMethod": "bank-transfer"}
    pending = call_tool(
        runtime.system, "source_execute", source_id="trend-coffee",
        operation="payment.initiate", arguments=arguments,
    )
    response = runtime.client.post(
        f'/v1/approvals/{pending["approval_id"]}/approve'
    )
    assert response.status_code == 200
    payment = call_tool(
        runtime.system, "source_execute", source_id="trend-coffee",
        operation="payment.initiate", arguments=arguments,
        approval_id=pending["approval_id"],
    )
    verified = call_tool(
        runtime.system, "source_verify", receipt_id=payment["receipt_id"]
    )
    assert verified["observed"] is True
    payment_snapshot = call_tool(
        runtime.system, "structured_query", source_id="trend-coffee",
        resource_type="payment", consistency="cached",
    )
    artifact = render_payment_artifact(runtime, payment_snapshot)
    assert artifact.manifest.provenance_receipt_id == payment["receipt_id"]
    assert "agent_supplied_qr" not in artifact.bundle.app_js


def test_scheduler_can_receive_only_source_sync(system):
    task = system.scheduler.create_task(
        prompt="Refresh Trend Coffee menu", schedule_type="once",
        schedule_value="2026-08-20T00:00:00+00:00", agent="orchestrator",
        tools="source_sync",
    )
    assert task.tools == "source_sync"
    with pytest.raises(ValueError, match="scheduler_tool_not_allowed"):
        system.scheduler.create_task(
            prompt="Place an order", schedule_type="once",
            schedule_value="2026-08-20T00:00:00+00:00",
            agent="orchestrator", tools="source_execute",
        )


def test_raw_http_shell_and_mcp_results_do_not_gain_data_plane_trust(system):
    before = system.data_plane.capabilities.list_source_ids()
    call_tool(system, "http_request", method="GET", url="https://provider.test/menu")
    call_tool(system, "shell_exec", command="printf structured-candidate")
    call_fixture_mcp_tool(system)
    assert system.data_plane.capabilities.list_source_ids() == before
```

- [ ] **Step 2: Verify red**

Run: `uv run pytest tests/integration/test_universal_data_plane.py tests/integration/test_artifact_ordering_loop.py tests/integration/test_data_plane_scheduler_policy.py -q`

Expected: FAIL until policy/preset and all real wiring agree.

- [ ] **Step 3: Rewrite Skill and system prompt to direct-first policy**

Replace browser-only rules with this ordered policy:

```text
structured_query cached/refresh_if_stale
-> source_sync through validated capability
-> source_discover when missing/stale
-> browser observation/fallback only when discovery reports it is required
```

Require separate mutation/observation turns, exact approval, `source_verify`, and `artifact_render` from verified snapshots/receipts. Retain Vietnamese voice brevity, variants, notes, branch, takeaway/dine-in, no silent substitution, no payment success without verified state, and no QR authored by the model.

Add one shared narrow validator used by both `TaskScheduler.create_task` and
`_execute_task`: if a scheduled task's tool list contains any Data Plane tool
other than `source_sync`, reject/fail it with `scheduler_tool_not_allowed`.
This also blocks a prohibited task already present in the database. Existing
non-Data-Plane scheduled tools retain their current behavior. This is a
structural guard, not prompt-only policy.

- [ ] **Step 4: Finalize the real preset**

The preset must contain:

```toml
[data_plane]
enabled = true
source_url = "https://trendcoffee.net/"
discovery_budget_seconds = 60
browser_fallback = true
trusted_write_operations = ""  # safe default; an authorized live run supplies exact entries in a temporary config

[merchants]
backend = "trendcoffee"
source_id = "trend-coffee"

[agent]
parallel_tools = false

[tools]
enabled = "source_discover,source_sync,structured_query,source_execute,source_verify,branch_list,menu_search,menu_item,cart_add,cart_remove,cart_view,order_place,order_verify,artifact_render,http_request,shell_exec"
```

Do not add browser click/type tools to the default Kiosk preset. `browser_fallback=true` enables the internal bounded observer only.

- [ ] **Step 5: Add the opt-in live harness without unattended mutation**

`scripts/e2e_universal_data_plane.py` accepts `--source`, `--kiosk-url`, `--read-only` (default), `--allow-order`, and `--approval-id`. Default behavior opens the event WebSocket, performs cold discovery/sync, prints stage timings, restarts/rebuilds the system, performs warm sync, asserts `browser_actions == 0`, publishes an artifact, and prints its URL. When `--kiosk-url` is supplied, launch one isolated Playwright page for UI acceptance, verify the candidate/ready/activated handshake, strict sandbox/CSP, and a visible normalized menu. This UI browser is reported separately and never counted as a provider-discovery browser action.

Derive the spec metrics from emitted events/traces and print a compact summary:
cold/warm latency, capability-cache hit, browser fallback/actions, snapshot
freshness/version, schema demotions, ambiguous mutations, and artifact
rejections/rollbacks. Do not add a parallel metrics service; EventBus/tracing
remain the source of observability data.

`--allow-order` must refuse unless both exact trusted write fingerprints and an approved action id are present. It prints the exact request preview and never retries a mutation.

- [ ] **Step 6: Document manual real-order/QR acceptance**

The runbook includes:

1. read-only cold run and expected capability/snapshot evidence;
2. process restart and warm zero-browser evidence;
3. operator inspection of fingerprint/contracts and temporary exact trust entry;
4. customer request “đặt cho tôi cà phê đen, 2 ly mang đi”;
5. exact cart/order approval;
6. one order POST and separate GET verification;
7. explicit bank-transfer approval, one payment initiation, verified merchant QR response;
8. browser inspection of sandbox attributes, CSP, generated artifact and bridge;
9. cleanup of temporary write trust after the authorized test.

- [ ] **Step 7: Run all focused automated gates**

Run each command separately:

```bash
uv run pytest tests/data_plane tests/artifacts -q
uv run pytest tests/merchants tests/tools/test_data_plane_tools.py tests/tools/test_source_execute_approval.py tests/tools/test_artifact_render.py -q
uv run pytest tests/tools/test_ordering_cart.py tests/tools/test_ordering_menu.py tests/tools/test_ordering_order.py tests/tools/test_ordering_doctrine.py -q
uv run pytest tests/system/test_data_plane_wiring.py tests/system/test_ordering_wiring.py -q
uv run pytest tests/server/test_artifact_routes.py tests/server/test_kiosk_artifact_host.py tests/server/test_ws_bridge.py -q
uv run pytest tests/integration/test_universal_data_plane.py tests/integration/test_artifact_ordering_loop.py tests/integration/test_data_plane_scheduler_policy.py -q
uv run ruff check src/openjarvis/data_plane src/openjarvis/artifacts src/openjarvis/merchants/trendcoffee.py src/openjarvis/tools/data_plane.py src/openjarvis/tools/artifact_render.py
```

Expected: all PASS; live tests remain skipped unless their explicit environment gates are set.

Run from `frontend/`:

```bash
npm test -- --run src/hooks/useArtifactHost.test.ts src/lib/viteProxy.test.ts
npm run build
```

Expected: PASS.

- [ ] **Step 8: Run read-only live and measure targets**

Run:

```bash
OPENJARVIS_LIVE_TREND_READ=1 uv run python scripts/e2e_universal_data_plane.py --source https://trendcoffee.net/ --read-only
```

For browser/WebSocket acceptance against the running stack, append
`--kiosk-url http://127.0.0.1:5173/kiosk`.

Expected: cold attempt at or under roughly 60 seconds, warm sync 5--30 seconds, `browser_actions=0` on warm, branch/menu snapshots non-empty, and—with `--kiosk-url`—an activated artifact visibly backed by the immutable localhost URL.

- [ ] **Step 9: Commit final policy and acceptance assets**

```bash
git add skills/serve-trend-coffee-customers/SKILL.md configs/openjarvis/prompts/ordering-kiosk.md configs/openjarvis/examples/ordering-kiosk.toml src/openjarvis/scheduler/scheduler.py tests/integration scripts/e2e_universal_data_plane.py docs/testing/universal-data-plane-live.md
git commit -m "test(data-plane): prove direct-first Trend Coffee flow"
```

## Final review gate

Before declaring the phase complete:

1. Run `git diff 778c13e..HEAD --check` and inspect every changed file against this plan.
2. Confirm `uv.lock`, `.codegraph/`, and the uncommitted research note were not accidentally staged by any task.
3. Run the focused gates from Task 14 separately; do not substitute one monolithic pytest command.
4. Review all task commits together for cross-task holes: duplicate browser runtimes, unguarded POST retries, write promotion without exact fingerprint, raw secrets in events, Artifact actions bypassing Agent, or Kiosk owning business state.
5. Perform the read-only live run and inspect actual WebSocket/artifact output.
6. Perform a real order/payment only with explicit operator and customer authorization; record the exact receipt/verification evidence with secrets and PII redacted.
