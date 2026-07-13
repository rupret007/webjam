package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"

	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/ipc"
)

var buildID = "dev"

func run() int {
	// No CLI configuration is accepted. Session material belongs on bounded
	// stdin IPC, never in argv or the environment.
	if len(os.Args) != 1 {
		return 2
	}
	if !icequic.RuntimeReady() {
		return 2
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := ipc.Run(ctx, os.Stdin, os.Stdout, buildID); err != nil {
		return 2
	}
	return 0
}

func main() { os.Exit(run()) }
