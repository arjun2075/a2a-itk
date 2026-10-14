# Fresh-connection AUTH_REQUIRED test validation

The upstream candidate is `SEC-AUTH-007` in A2A's
`tests/acts/auth-security.acts.yaml`. It is based on the still-unmerged
`conformance-spec-adjustments` branch, not the main-branch corpus. It adds an
ACTS grammar feature (`fresh_connection`) and therefore requires review of
both the format change and the companion ITK implementation. Nothing has been
submitted upstream in this work.

The test creates a task with `tck-auth-required`, captures task/context IDs,
then calls `GetTask` on an independent HTTP client and connection pool. It
asserts the same task/context IDs, AUTH_REQUIRED state, and a status message.
The fixture is explicitly held pending until authorization is supplied.
It makes no requirements about approval signatures, expiry, replay handling,
resumption, or delegation.

## Validation

- 22 focused checks passed, with no skips.
- 339 relevant runner/schema/dispatcher/pipeline/end-to-end checks passed.
- The complete proposed upstream corpus loaded: 114 tests. The upstream
  candidate is identical to the fixture used in the network tests.
- The official Python SDK was built at commit
  `0ee47d7baa47a706e33934a4e4c3d46bad16298f`
  (`1.2.2.post10.dev0+0ee47d7`). This is a pinned upstream development revision,
  not a claim of validation across all SDKs or a released SDK version.
- Positive controls passed over JSON-RPC and HTTP+JSON. Server-side socket
  evidence confirms different TCP connections. Bearer credentials, version
  headers, and a cookie set by SendMessage are retained on GetTask.
- Five response mutations per binding fail: stale SUBMITTED state, wrong task
  ID, wrong context ID, absent status message, and TaskNotFound response.
- A runner mutation that reuses its old client passes response assertions but
  is caught by the socket check on both bindings. This guards the precise
  execution gap in the original H09 sketch.
- Missing runner capability and non-reconstructible injected HTTP clients
  skip without sending any requests. Six malformed format contracts reject.
- The existing `scenarios/acts` mirror remains unchanged. The proposed test is
  stored under `tests/fixtures/h09` only for supporting runner validation.

## Reproduce

From the ITK checkout, with a Python virtual environment containing its usual
dependencies, install the pinned SDK and network-fixture dependencies:

```sh
pip install 'a2a-sdk[http-server] @ git+https://github.com/a2aproject/a2a-python.git@0ee47d7baa47a706e33934a4e4c3d46bad16298f' uvicorn
python -m pytest tests/test_acts_fresh_connection.py -q -s
```

The fixture starts the SDK server in a separate process because ITK and the
SDK load different generated `a2a.proto` descriptors. Server processes and
independent client pools are closed after each test, including failures.

`results.json` records the revision pins and observed socket/credential data.
Credentials in those records are synthetic fixture values.

## Limits

This candidate covers unary HTTP transports only. It does not exercise gRPC,
HTTP/2, restart persistence, multi-process server storage, or expired/revoked
credentials. The grammar specifies that an HTTP/2 stream on an existing
connection would not count as fresh, but that case was not separately run.
The ITK implementation supports its normally owned clients; it skips injected
clients rather than attempting to reconstruct custom TLS/auth/proxy settings.
