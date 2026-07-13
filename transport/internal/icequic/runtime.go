package icequic

import (
	"github.com/pion/ice/v4"
	"github.com/rupret007/webjam/transport/internal/limits"
)

// RuntimeReady is a startup invariant for the static command. Besides failing
// closed on accidental protocol/config drift, it keeps the selected ICE/QUIC
// implementation in the command's compiled dependency boundary even while
// external rendezvous commands are still a later slice.
func RuntimeReady() bool {
	config := QUICConfig()
	return ALPN == "webjam/3" && limits.WireVersion == 3 && config.EnableDatagrams &&
		!config.Allow0RTT && config.MaxIncomingStreams == limits.MaxConcurrentStreams &&
		ice.CandidateTypeRelay.String() == "relay" && ice.NetworkTypeUDP4.IsUDP()
}
