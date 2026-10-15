- Extended the optional Cross-network acceptance harness with a real public-ns
  STUN server, cone vs symmetric NAT cases, and a documented test-only STUN
  name override (netns hosts; never active in packaged builds) so PR #372
  direct-stun Host/Join can be driven without editing that branch.
