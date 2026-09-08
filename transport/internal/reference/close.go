package reference

import (
	"context"

	"github.com/rupret007/webjam/transport/internal/limits"
)

// CloseLocalSession makes one authenticated host-close attempt on a fresh
// reference-local control connection. The caller retains ownership of its
// token and supplies the next sequence for the existing session; this function
// neither enrolls nor retries. A missing acknowledgment leaves service removal
// unconfirmed, even though this function closes its own control connection.
func CloseLocalSession(
	ctx context.Context,
	session SessionID,
	hostToken *RoleToken,
	generation uint32,
	sequence uint64,
) error {
	return closeLocalSession(ctx, session, hostToken, generation, sequence, DialLocal)
}

func closeLocalSession(
	ctx context.Context,
	session SessionID,
	hostToken *RoleToken,
	generation uint32,
	sequence uint64,
	dial func(context.Context) (*Client, error),
) (result error) {
	if ctx == nil || dial == nil || !validAuthenticated(session, RoleHost, hostToken, generation, sequence) {
		return ErrInvalidInput
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	// Dial and acknowledgment share one total budget, including when the
	// caller supplied no deadline. A shorter caller deadline always wins.
	closeCtx, cancel := context.WithTimeout(ctx, limits.ShutdownLimit)
	defer cancel()
	client, err := dial(closeCtx)
	if client != nil {
		defer func() {
			if closeErr := client.Close(); result == nil && closeErr != nil {
				result = closeErr
			}
		}()
	}
	if err != nil || client == nil {
		if contextErr := closeCtx.Err(); contextErr != nil {
			return contextErr
		}
		// Keep dial/system error text outside this API's error surface.
		return ErrControlUnavailable
	}
	return client.CloseSession(closeCtx, session, RoleHost, hostToken, generation, sequence)
}
