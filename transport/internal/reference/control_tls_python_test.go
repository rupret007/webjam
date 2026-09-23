package reference

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestTLSIntegrationPythonVersionBoundary(t *testing.T) {
	root, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	python, discovery := findTLSIntegrationPython(root)
	if python == "" {
		if os.Getenv("WEBJAM_REQUIRE_TLS_INTEGRATION") == "1" {
			t.Fatalf("Python 3.10+ is required for this TLS integration run: %s", discovery)
		}
		t.Skipf("Python unavailable for version probe test: %s", discovery)
	}
	for _, tc := range []struct {
		name    string
		version string
		wantErr bool
	}{
		{"below_minimum", "3, 9, 99", true},
		{"minimum", "3, 10, 0", false},
		{"newer", "3, 11, 9", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			script := "import sys; sys.version_info = (" + tc.version + "); " + tlsPythonVersionProbe
			err := runTLSIntegrationPythonProbe(ctx, exec.CommandContext(ctx, python, "-c", script))
			if (err != nil) != tc.wantErr {
				t.Fatalf("version probe error = %v; want error %v", err, tc.wantErr)
			}
			if tc.wantErr && !strings.Contains(err.Error(), "Python 3.9.99") {
				t.Fatalf("rejected version missing from diagnostics: %v", err)
			}
		})
	}
}

func TestTLSIntegrationPythonProbeFailureDiagnostics(t *testing.T) {
	t.Run("missing_executable", func(t *testing.T) {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		missing := filepath.Join(t.TempDir(), "missing-python")
		err := runTLSIntegrationPythonProbe(ctx, exec.CommandContext(ctx, missing, "-c", tlsPythonVersionProbe))
		if err == nil || !strings.Contains(err.Error(), "version probe failed") || !strings.Contains(err.Error(), "missing-python") {
			t.Fatalf("missing executable diagnostics = %v", err)
		}
	})
	t.Run("expired_deadline", func(t *testing.T) {
		ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
		defer cancel()
		err := runTLSIntegrationPythonProbe(ctx, exec.CommandContext(ctx, "python", "-c", tlsPythonVersionProbe))
		if !errors.Is(err, context.DeadlineExceeded) || !strings.Contains(err.Error(), "version probe deadline exceeded") {
			t.Fatalf("deadline diagnostics = %v", err)
		}
	})
}
