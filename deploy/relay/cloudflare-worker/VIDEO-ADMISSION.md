# Private WebJam video relay admission

The video credential route requires the internal `VIDEO_ADMISSION` HTTP
service binding in addition to the dedicated video relay key and budgets. A
private session authority must answer this request on every issuance,
including renewal. Cloudflare service bindings pass requests directly between
Workers. [HTTP service binding reference](https://developers.cloudflare.com/workers/runtime-apis/bindings/service-bindings/http/)

The issuer sends a JSON POST to `/v1/check` with `session_id` and
`invite_digest`. The digest is lowercase hex SHA-256 of the exact
UTF-8 invite proof supplied by the browser. The authority must check the
pair against the current, unexpired, unrevoked authorization for a private
WebJam session. A trusted admission flow must establish the entry before a
browser can ask for relay credentials. Keep the verifier and its admission
records private. Retain only the digest for lookup, and keep request bodies
out of logs.

The authority returns an empty 204 for an active admission and a 403
otherwise. Missing or unavailable binding, timeout, redirect and unexpected
status keep the video issuer closed. The issuer checks again on renewal
without caching a prior approval. Existing relay credentials retain their
provider lifetime. The browser also closes its video peer when the invite
expires or the session ends.

The reviewed source supplies the issuer check. It needs a separately reviewed
private session authority and runtime binding before live activation. The
repository configuration leaves the binding absent. Music uses its existing
issuer path.
