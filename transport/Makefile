GO ?= go
OUT ?= build
VERSION ?= dev
LDFLAGS = -s -w -X main.buildID=$(VERSION)

.PHONY: fmt test vet inventory check build-all build-darwin-arm64 build-darwin-amd64 build-windows-amd64 build-linux-amd64 clean

fmt:
	@files="$$(find . -type f -name '*.go' -not -path './build/*' -exec gofmt -l {} +)"; test -z "$$files" || { echo "$$files"; exit 1; }

test:
	$(GO) test ./...

vet:
	$(GO) vet ./...

inventory:
	@actual="$$(mktemp)"; documented="$$(mktemp)"; \
	trap 'rm -f "$$actual" "$$documented"' EXIT; \
	$(GO) list -deps -f '{{with .Module}}{{if ne .Path "github.com/rupret007/webjam/transport"}}{{.Path}} {{.Version}}{{end}}{{end}}' ./cmd/webjam-fabric \
		| sed '/^$$/d' | sort -u > "$$actual"; \
	sed -n 's/^| `\([^`]*\)` | `\([^`]*\)` |.*$$/\1 \2/p' DEPENDENCIES.md \
		| sort -u > "$$documented"; \
	diff -u "$$documented" "$$actual"

check: fmt test vet inventory

build-all: build-darwin-arm64 build-darwin-amd64 build-windows-amd64 build-linux-amd64

build-darwin-arm64:
	mkdir -p $(OUT)/darwin-arm64
	CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 $(GO) build -trimpath -ldflags='$(LDFLAGS)' -o $(OUT)/darwin-arm64/webjam-fabric ./cmd/webjam-fabric

build-darwin-amd64:
	mkdir -p $(OUT)/darwin-amd64
	CGO_ENABLED=0 GOOS=darwin GOARCH=amd64 $(GO) build -trimpath -ldflags='$(LDFLAGS)' -o $(OUT)/darwin-amd64/webjam-fabric ./cmd/webjam-fabric

build-windows-amd64:
	mkdir -p $(OUT)/windows-amd64
	CGO_ENABLED=0 GOOS=windows GOARCH=amd64 $(GO) build -trimpath -ldflags='$(LDFLAGS)' -o $(OUT)/windows-amd64/webjam-fabric.exe ./cmd/webjam-fabric

build-linux-amd64:
	mkdir -p $(OUT)/linux-amd64
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 $(GO) build -trimpath -ldflags='$(LDFLAGS)' -o $(OUT)/linux-amd64/webjam-fabric ./cmd/webjam-fabric

clean:
	rm -rf $(OUT)
