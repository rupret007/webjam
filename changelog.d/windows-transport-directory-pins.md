Fix Windows transport directory pins so ancestor delete and rename attempts are refused while a process is launched. Directory handles request list access; refusal still cleans up opened handles and child processes.

Preserve pinned artifact hashes, trusted native verifier paths and fail-closed ACL checks. Add native Windows sharing controls and portable launch-cleanup coverage.
