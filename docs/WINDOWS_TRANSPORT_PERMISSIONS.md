# Windows transport permissions contract

Source implementation and OS-isolated tests only, 2026-10-25. **Native Windows
API behavior, a frozen installed package and real Authenticode remain
unqualified. This change does not make the unsigned Windows beta available.**
Base: `83af86e6bf3747e362db90870f09a928130623a2` (PR401). No signing purchase,
installation, credential, provider, dependency constraint or release change.

## Why this is separate from a signature

Windows Python mode bits cannot implement the POSIX group/other-write test.
[Python documents that Windows chmod controls the read-only attribute](https://docs.python.org/3.11/library/os.html#os.chmod).
The source-bound baseline probe uses the Windows module branch and a synthetic
PE file with actual POSIX 0777 metadata: it is rejected before signature checking.
It is a control-flow reproduction, not an observation on a Windows machine.

`WindowsBinaryGuard` replaces the 022 predicate only on Windows. Existing POSIX
ownership, write and execute checks stay intact. Hash, PE architecture,
mandatory frozen-build signature verification, constant argv and isolated child
environment are retained. A permission refusal gives a bounded reinstall/admin
message, without paths, ACL contents or verifier output.

## Accepted authority and objects

- The owner of the executable and **each lexical ancestor** must be the current
  process TokenUser SID, LocalSystem, Builtin Administrators, or the locally
  resolved `NT SERVICE\TrustedInstaller` service SID. Administrator membership
  does not make an unrelated group safe. An untrusted owner is rejected even
  if its present DACL contains no write ACE, because ownership can permit DACL
  changes. This boundary does not protect against malicious code already
  running as the same trusted user or installation administrator.
- The volume must be local/fixed and report persistent ACL support. 128-bit
  FileIdInfo must be available. No filesystem-name assumption is made: NTFS and
  ReFS can qualify through these APIs; remote/removable volumes and missing
  capabilities remain unsupported. The file must have one link and be at most
  128 MiB. Paths are bounded to 64 components/32767 characters.
- Reject UNC/device/extended paths, streams, reserved names, parent traversal,
  ambiguous trailing spaces/dots, reparse points at any checked component,
  changed file IDs/final paths, unreadable descriptors and null/missing DACLs.
- Only ordinary allow/deny ACEs with supported flags and masks qualify.
  Applying allow ACEs must not grant other principals file write/append,
  attribute/EA modification, DELETE, WRITE_DAC or WRITE_OWNER. Ancestors also
  reject DELETE_CHILD and WRITE_DATA/ADD_FILE. Ancestor ADD_SUBDIRECTORY alone
  does not replace the existing nonempty child chain. Both file DELETE and
  parent DELETE_CHILD are checked. Generic rights are mapped explicitly;
  MAXIMUM_ALLOWED, system-security, callback/object/conditional or unknown forms
  are refused. Inherit-only ACEs do not apply to their current object. A deny
  ACE is never subtracted to manufacture acceptance of an unsafe allow ACE.

These conservative rules can refuse an installation Windows itself could use.
That is an actionable unsupported case, not a reason to bypass verification.
[Microsoft describes file security, ownership and access rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights),
[generic/specific file rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-access-rights-constants),
and [the difference between null and empty DACLs](https://learn.microsoft.com/en-us/windows/win32/secauthz/null-dacls-and-empty-dacls).

## Replacement fence and scope of tests

The adapter uses CreateFileW with OPEN_EXISTING, OPEN_REPARSE_POINT,
BACKUP_SEMANTICS, no inheritable security attributes and FILE_SHARE_READ only.
The guard opens root-to-leaf before path resolution, reads handle-bound owner,
DACL, attributes and 128-bit identity, then checks those again after hash,
architecture and signature validation. The handles remain open through Popen's
CreateProcess call; they never enter the child. Existing writers/deleters cause
sharing refusal. Security descriptors are inspected read-only and freed.

[GetSecurityInfo explicitly supplies no race protection itself](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo).
[CreateFileW documents read/write/delete sharing and its lifetime](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew).
The guard relies on the Windows handle/share contract, not repeated path stats.

Tests cover trusted and untrusted ownership, specific/generic mutation rights,
inherit-only/inherited ACEs, deny-order conservatism, unsupported forms/volumes,
reparse/identity/replacement controls, partial-failure cleanup, native API
argument construction, 0777 reaching mandatory signature only after synthetic
ACL acceptance, and no spawn after permission/hash/architecture/signature
refusal. Raw synthetic SID/DACL/ACE buffers also exercise the real Python
decoder's bounds, allocation cleanup and metadata layouts. Every guard handle
gets a cleanup attempt; failed cleanup denies the launch and terminates an
already-created child. Existing transport/signature tests remain included.
The focused run passed 118 cases (72 permission/cleanup controls plus 46 existing
transport cases). These tests use fake Win32 results on macOS and do not
establish native ABI/API correctness.

Before a Windows release, run a real isolated Windows diagnostic with ordinary
and read-only files, restrictive/permissive/inherited DACLs, owner/DACL changes,
parent deletion/replacement, reparse/hardlink cases, existing writer handles and
both successful and failed CreateProcess/signature results. Verify native
structure layouts and API cleanup, actual installed package/CPython version and
handle lifetimes; then separately qualify signing, app installation and audio.
Keep unsigned Windows availability and all existing release holds unchanged.
