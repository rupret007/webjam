# WebJam fabric third-party notices

The WebJam fabric source module pins the following direct dependencies. Its
statically linked transitive modules are listed in `DEPENDENCIES.md`.

| Module | Version | License |
|---|---:|---|
| `github.com/pion/ice/v4` | `v4.3.0` | MIT |
| `github.com/pion/logging` | `v0.2.4` | MIT |
| `github.com/pion/stun/v3` | `v3.1.6` | MIT |
| `github.com/pion/transport/v4` | `v4.0.2` | MIT |
| `github.com/pion/turn/v5` | `v5.0.12` | MIT |
| `github.com/quic-go/quic-go` | `v0.60.0` | MIT |

The Pion projects use the text in `licenses/PION-MIT.txt`. quic-go uses
`licenses/QUIC-GO-MIT.txt`. Google UUID uses
`licenses/GOOGLE-UUID-BSD-3-CLAUSE.txt`; anet uses
`licenses/ANET-BSD-3-CLAUSE.txt`. The Go compiler/runtime and linked
`golang.org/x/*` modules use the BSD-style text in
`licenses/GO-BSD-3-CLAUSE.txt`.

`go.sum` pins the full dependency graph used for compilation and tests.
`go mod verify`, `go mod tidy -diff`, the race suite, and the binary dependency
inventory are release gates. Test-only modules are not linked into the shipped
binary and remain recorded by `go.sum`.
