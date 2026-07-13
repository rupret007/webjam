// Package limits contains the resource ceilings shared by the transport
// process. They are protocol behavior, not tuning hints: callers must fail
// closed or drop live datagrams when a ceiling is reached.
package limits

import "time"

const (
	IPCVersion             = 1
	WireVersion            = 3
	MaxIPCLineBytes        = 64 * 1024
	MaxEventLineBytes      = 4 * 1024
	MaxLivePayloadBytes    = 1024
	MaxDatagramQueueDepth  = 64
	MaxStreamFrameBytes    = 64 * 1024
	MaxStreamFrames        = 16_384
	MaxStreamBytes         = 1024 * 1024 * 1024
	MaxConcurrentStreams   = 4
	MaxBuildIDBytes        = 96
	MaxICEServerCount      = 8
	MaxCandidateCount      = 32
	MaxCandidateBytes      = 2048
	MaxICECredentialBytes  = 256
	MaxSignalEnvelopeBytes = 16 * 1024
	MaxCertificateBytes    = 4 * 1024
	MaxReplayNonces        = 1024
)

const (
	SocketPollInterval    = 100 * time.Millisecond
	StreamOperationLimit  = 15 * time.Second
	HandshakeLimit        = 15 * time.Second
	ShutdownLimit         = 3 * time.Second
	MaxIdentityLifetime   = 24 * time.Hour
	MinIdentityLifetime   = time.Minute
	HostIdentityLifetime  = 8 * time.Hour
	MaxSignalLifetime     = 10 * time.Minute
	MaxEnrollmentLifetime = 10 * time.Minute
	EnrollmentClockSkew   = 30 * time.Second
)
