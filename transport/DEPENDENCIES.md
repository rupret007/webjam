# WebJam fabric linked dependency inventory

Generated from the pinned Go module with:

```sh
go list -deps -f '{{with .Module}}{{.Path}} {{.Version}}{{end}}' \
  ./cmd/webjam-fabric | sort -u
```

The release gate also runs `go mod verify` and `go mod tidy -diff`. These are
the non-standard-library modules linked into `webjam-fabric`:

| Module | Version | License notice |
|---|---:|---|
| `github.com/google/uuid` | `v1.6.0` | `GOOGLE-UUID-BSD-3-CLAUSE.txt` |
| `github.com/pion/dtls/v3` | `v3.1.5` | `PION-MIT.txt` |
| `github.com/pion/ice/v4` | `v4.3.0` | `PION-MIT.txt` |
| `github.com/pion/logging` | `v0.2.4` | `PION-MIT.txt` |
| `github.com/pion/mdns/v2` | `v2.1.0` | `PION-MIT.txt` |
| `github.com/pion/randutil` | `v0.1.0` | `PION-MIT.txt` |
| `github.com/pion/stun/v3` | `v3.1.6` | `PION-MIT.txt` |
| `github.com/pion/transport/v4` | `v4.0.2` | `PION-MIT.txt` |
| `github.com/pion/turn/v5` | `v5.0.12` | `PION-MIT.txt` |
| `github.com/quic-go/quic-go` | `v0.60.0` | `QUIC-GO-MIT.txt` |
| `github.com/wlynxg/anet` | `v0.0.5` | `ANET-BSD-3-CLAUSE.txt` |
| `golang.org/x/crypto` | `v0.51.0` | `GO-BSD-3-CLAUSE.txt` |
| `golang.org/x/net` | `v0.55.0` | `GO-BSD-3-CLAUSE.txt` |
| `golang.org/x/sys` | `v0.45.0` | `GO-BSD-3-CLAUSE.txt` |
| `golang.org/x/time` | `v0.14.0` | `GO-BSD-3-CLAUSE.txt` |

The Go compiler/runtime license is also included as
`GO-BSD-3-CLAUSE.txt`. Test-only modules are intentionally excluded from this
binary-linked inventory; their exact versions remain pinned by `go.sum`.
