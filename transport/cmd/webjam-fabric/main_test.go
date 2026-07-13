package main

import (
	"io"
	"os"
	"strings"
	"testing"
)

func TestRunRejectsAllCommandLineConfiguration(t *testing.T) {
	oldArgs := os.Args
	os.Args = []string{"webjam-fabric", "--enrollment-capability=PRIVATE-ARGV-SENTINEL"}
	t.Cleanup(func() { os.Args = oldArgs })
	if code := run(); code != 2 {
		t.Fatalf("exit code = %d", code)
	}
}

func TestRunIgnoresEnvironmentConfigurationWithoutEcho(t *testing.T) {
	const sentinel = "PRIVATE-ENVIRONMENT-SENTINEL"
	for _, name := range []string{
		"WEBJAM_ENROLLMENT_CAPABILITY", "WEBJAM_PROFILE_ID", "WEBJAM_RENDEZVOUS_URL", "WEBJAM_HOST_SPKI_SHA256",
	} {
		t.Setenv(name, sentinel)
	}
	stdinReader, stdinWriter, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	stdoutReader, stdoutWriter, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	oldArgs, oldStdin, oldStdout := os.Args, os.Stdin, os.Stdout
	t.Cleanup(func() {
		os.Args, os.Stdin, os.Stdout = oldArgs, oldStdin, oldStdout
		_ = stdinReader.Close()
		_ = stdoutReader.Close()
		_ = stdoutWriter.Close()
	})
	os.Args = []string{"webjam-fabric"}
	os.Stdin = stdinReader
	os.Stdout = stdoutWriter
	if _, err = io.WriteString(stdinWriter, "{\"version\":1,\"id\":1,\"type\":\"shutdown\"}\n"); err != nil {
		t.Fatal(err)
	}
	if err = stdinWriter.Close(); err != nil {
		t.Fatal(err)
	}
	if code := run(); code != 0 {
		t.Fatalf("exit code = %d", code)
	}
	if err = stdoutWriter.Close(); err != nil {
		t.Fatal(err)
	}
	os.Stdout = oldStdout
	output, err := io.ReadAll(stdoutReader)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(output), sentinel) || strings.Contains(string(output), "WEBJAM_") {
		t.Fatalf("environment leaked into output: %s", output)
	}
}
