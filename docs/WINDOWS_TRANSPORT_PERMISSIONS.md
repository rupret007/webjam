# Windows transport permissions contract

Source implementation, OS-isolated tests and native diagnostic definition,
2026-10-25. **The earlier eight-case Windows control result is separate from
qualification of the explicit command below, whose Windows CI rerun is pending.
A frozen installed package and real Authenticode remain unqualified. This change
does not make the unsigned Windows beta available.**
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

Directory pins request FILE_LIST_DIRECTORY as well as READ_CONTROL and
FILE_READ_ATTRIBUTES: metadata-only opens do not activate read sharing
accounting. A directory without the required list access is refused; there is
no metadata-only fallback or ACL repair. The explicit Windows control
`python -X dev -m tests.support.windows_directory_sharing_control` proves
directory-only fencing without a leaf handle, reverse-order DELETE conflicts,
full-guard ancestor deletion/rename and recovery after release. It runs in the
existing unconditional Windows diagnostic job and refuses selectors, skips
and non-Windows execution. Only its newly created private lab receives ACL
changes; real parent directories remain unchanged. This does not qualify
signing, installation, audio or all other native diagnostic cases.

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

## Bounded actual Windows diagnostic

The separate follow-up based on
`005d72a278b68519fa6209bc9933f85eb0ebd902` adds eight native cases to the existing
unconditional `windows-diagnostic-control` job. It adds no job, dispatch,
dependency, installer, signature exception or release action. The three-minute
step runs even when earlier controls fail and retains its JUnit, source/hash and
environment receipts with the existing control artifacts. A failing native
control still fails the job. A follow-up moves those unchanged case bodies into
an explicit support command, outside default portable pytest discovery, without
a skip/xfail marker or integrity-check exception.

```text
python -X dev -m tests.support.windows_transport_permissions_native_control
```

The command accepts no options, including help, output-path or selection flags.
It refuses inherited `PYTEST_ADDOPTS`/`PYTEST_PLUGINS`, disables plugin autoload
while running, and uses a new empty configuration and no product conftest.
Only a fresh `out/windows-transport-acl-native/` can be reserved; existing,
non-directory, aliased or reparse targets are refused without cleaning them.
JUnit (`results.xml`), pytest temporary/cache files, configuration and execution
proof stay inside that new owned directory alongside the environment receipt.

A separate bounded plugin requires the exact eight case IDs and one passing
setup, call and teardown report for each, with no skips, expected failures,
duplicates or changed source hashes. Collection-only/subset runs and a runner's
`SystemExit(0)` cannot return qualification. Both pytest's actual result and this
`execution.json` proof must succeed before the command returns zero.

On macOS or Linux the CLI exits 2 with an explicit unsupported-host message before
loading pytest; this is neither a passing control nor a skipped native suite.
Ordinary recursive pytest uses its existing discovery rules and does not collect
the support command. Top-level portable controls cover CI wiring, non-overwrite
refusals and report/exit classification using benign temporary source copies and
synthetic runner reports. Those controls do not execute native Windows APIs.

The cases load the unchanged production guard directly by file path, without
the `services` initializer, product dependencies, injected API or synthetic
descriptors. All ACL changes apply only to a new unique lab tree beneath the
existing per-user `LOCALAPPDATA`. The system `icacls.exe` is found through
GetSystemDirectoryW and invoked with exact arguments, no shell and a ten-second
timeout. Existing ancestors, source interpreter/DLLs and system ACLs are never
modified. Every real ancestor must pass the actual guard; an unsuitable runner
ancestry is a retained failure, not a skipped or repaired control.

Intended coverage includes ordinary Windows writable mode bits and read-only
attributes with safe ACLs; actual file IDs, owner SIDs and DACL decoding;
Everyone-write file and Everyone-delete-child parent refusals; conflicting
writer, write/delete/rename sharing refusals; cleanup followed by successful
write/delete/rename; and a successful copied CI Python `--version` process while
the executable guard remains held. The Python probe copies at most four
adjacent interpreter runtime DLLs into the owned tree and supplies only
`SystemRoot` to the child. It does not launch WebJam, exercise the transport's
isolated environment, prove package installation, verify Authenticode or test
audio. The production guard and launch path are unchanged by this follow-up.

Earlier macOS receipts with two static passes/eight skips describe the initial
diagnostic definition and remain historical evidence; they do not describe this
explicit command. Application evidence separately retains an eight-case actual
Windows pass from job `121302109950`, run `40237418806`, before this command change.
The unchanged case-body comparison preserves its narrow meaning; the new command
and CI path still require a normal reviewed Windows CI run. Local portable checks
and an unsupported-host exit cannot establish native acceptance.

The source/environment receipt records intended coverage before test execution;
only the actual Windows pytest exit code and retained JUnit establish which
controls passed. Neither that earlier control nor this follow-up qualifies a
frozen WebJam application, package installation, Authenticode or physical audio.
All broader release requirements above remain in force.
