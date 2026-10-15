# Cross-network acceptance (proof-only)

Three jobs must **PASS**. Any non-success is FAIL. There is no expected-FAIL
and no honest-unreachable taxonomy.

| Case | Proof |
| --- | --- |
| `same-lan` | Direct connect, host/host pair on both peers |
| `cone` | Two endpoint-independent NATs; selected pair is **not** relay |
| `relay` | Two symmetric NATs plus coturn on the public net; at least one selected candidate is relay **and** coturn logged relayed bytes |

PASS requires Music (audio frame) and Art (stroke) payloads both ways with a
per-run nonce echoed back, plus selected candidate types from **both** peers.
Missing or unknown fields fail. Receipt `.PASS` is deleted at start and written
atomically only after every assertion.

NAT mapping is probed (one inside socket, two STUN endpoints) before each
cross-network case. Cone routers use explicit SNAT (not MASQUERADE). Symmetric
`--random-fully` is accepted only if the probe sees different ports. Routers
default-DROP INPUT/FORWARD and allow only ESTABLISHED,RELATED back in.

Deferred (follow-up): pion vnet in-process tier; hairpin and mixed cone/symmetric
cases.

Physical two-home Join, signing, and notarization are **NOT RUN**.
