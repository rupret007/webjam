"""Explicit selected-media backups and journalled, private workspace restoration.

Importing this module uses only the standard library and metadata codecs. Audio
and Studio consumers are loaded only during an explicitly selected operation.
No function starts playback, opens an external app, or scans a take's parent.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import dataclass, field, replace
from functools import wraps
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
from uuid import uuid4
import zipfile

from core.file_io import atomic_write_bytes, _fsync_parent_directory
from core.session_library import (
    SessionLibrary, SessionLibraryConflict, SessionLibraryError,
    SessionLibraryImportUnconfirmed, SessionRecord, decode_session_record,
    encode_session_record, _read,
)
from core.workspace_backup import (
    WorkspaceBackupError, WorkspaceBackupPreview, _canonical, _constant,
    _new_stage, _pairs, _record_bytes, _same_inode,
    prepare_workspace_backup_import,
)
from core.workspace_media_schema import (
    MAX_PACKAGE_BYTES, MAX_PACKAGE_FILES, MAX_PACKAGE_METADATA_BYTES,
    digest, file_inventory, relative_path,
)

_FORMAT = "webjam.workspace-media"
_MANIFEST = "workspace.json"
_OWNER = ".webjam-restore-owner.json"
_CHUNK = 1024 * 1024
_SIDECARS = ((".webjam-review.json", "review", 64 * 1024),
             (".webjam-studio-state.json", "studio", 8 * 1024**2),
             (".webjam-studio-state.json.bak", "studio", 8 * 1024**2),
             (".webjam-studio-state.v1.json.bak", "studio", 8 * 1024**2))


class WorkspacePackageCancelled(WorkspaceBackupError):
    """The user cancelled; a pending import retains exact recovery evidence."""


class WorkspacePackagePublicationUnconfirmed(WorkspaceBackupError):
    """A backup destination may exist; inspect these intended bytes before retry."""

    def __init__(self, destination, expected_sha256, expected_size, publication_receipt=None):
        super().__init__("Backup publication is uncertain. A destination may exist; check its exact checksum before retrying.")
        self.destination = Path(destination)
        self.expected_sha256 = expected_sha256
        self.expected_size = expected_size
        self.publication_receipt = publication_receipt


def _domain_errors(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except SessionLibraryError:
            raise
        except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError, zipfile.BadZipFile) as exc:
            raise WorkspaceBackupError(f"Workspace media operation could not finish safely: {exc}") from exc
    return call


@dataclass(frozen=True)
class WorkspaceBackupProgress:
    phase: str
    completed_bytes: int
    total_bytes: int
    path: str = ""


class _Work:
    def __init__(self, phase, total=0, progress=None, cancel_check=None):
        self.phase, self.total, self.done = phase, total, 0
        self.progress, self.cancel_check = progress, cancel_check

    def check(self):
        if self.cancel_check is not None and self.cancel_check():
            raise WorkspacePackageCancelled("Workspace media operation cancelled; existing work is unchanged.")

    def add(self, path, count):
        self.check()
        self.done += count
        if self.progress is not None:
            self.progress(WorkspaceBackupProgress(self.phase, self.done, self.total, str(path)))


@dataclass(frozen=True)
class WorkspaceBackupCandidate:
    kind: str
    reference_id: str
    title: str
    locator: str
    selectable: bool
    reason: str = ""


@dataclass(frozen=True)
class WorkspacePackageSelection:
    take_ids: tuple[str, ...] = ()
    art_reference_ids: tuple[str, ...] = ()
    include_reviews: bool = True
    include_studio: bool = True


@_domain_errors
def workspace_backup_candidates(record: SessionRecord) -> tuple[WorkspaceBackupCandidate, ...]:
    """List stored choices without probing any path, including network paths."""
    record = WorkspaceBackupPreview(_record_bytes(record)).record
    rows = []
    seen = set()
    for ref in record.take_links:
        key = ref.get("take_id", "")
        if key in seen:
            continue
        seen.add(key)
        path = ref.get("take_path", ref.get("path", ""))
        ready = bool(key and path)
        rows.append(WorkspaceBackupCandidate("take", key, ref.get("title", "Recording"), path,
                    ready, "Completion and media will be checked after selection." if ready else "No completed take location is stored."))
    for ref in record.art.get("references", []):
        ready = ref["kind"] == "file"
        rows.append(WorkspaceBackupCandidate("art", ref["id"], ref["title"], ref["locator"], ready,
                    "Local file will be checked after selection." if ready else "Web links remain metadata only; no download."))
    return tuple(rows)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _directory(path: Path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise WorkspaceBackupError("Media roots and parent folders must be real directories.")
    return info.st_dev, info.st_ino


@contextmanager
def _pin_windows_directory(path):
    """Deny deletion/renaming while a Windows directory pathname is in use."""
    import ctypes
    from ctypes import wintypes

    before = path.lstat()
    _directory(path)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    # FILE_READ_ATTRIBUTES, read/write sharing (deliberately NO delete share),
    # OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT.
    handle = create(str(path), 0x80, 3, None, 3, 0x02000000 | 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        class FileIdInfo(ctypes.Structure):
            _fields_ = [("volume", ctypes.c_ulonglong), ("identifier", ctypes.c_ubyte * 16)]
        get_info = kernel.GetFileInformationByHandleEx
        get_info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        get_info.restype = wintypes.BOOL
        info = FileIdInfo()
        if not get_info(handle, 18, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        identifier = int.from_bytes(bytes(info.identifier), "little")
        # Python 3.11 exposes the legacy64-bit file index;3.12+ may expose128.
        if before.st_ino not in {identifier, identifier & ((1 << 64) - 1)} or _directory(path) != (before.st_dev, before.st_ino):
            raise WorkspaceBackupError("Media directory changed while its identity was pinned.")
        yield
    finally:
        close(handle)


@contextmanager
def _bound_parent(root, name, *, create=False, created_paths=None):
    """Bind every destination component; links never redirect a file operation."""
    relative_path(name)
    root = Path(root).absolute()
    parts = name.split("/")
    with ExitStack() as stack:
        if os.name == "nt":
            # Pin all ancestors as well: renaming an ancestor must not redirect
            # the pathname through which Windows creates the final child.
            for parent in reversed((root, *root.parents)):
                stack.enter_context(_pin_windows_directory(parent))
            current = root
            for part in parts[:-1]:
                current /= part
                if create:
                    try:
                        current.mkdir(mode=0o700)
                        if created_paths is not None:
                            created_paths.append(current)
                    except FileExistsError:
                        pass
                stack.enter_context(_pin_windows_directory(current))
            yield None, current, parts[-1]
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        before = _directory(root)
        descriptor = os.open(root, flags)
        stack.callback(os.close, descriptor)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != before:
            raise WorkspaceBackupError("Media root changed while opening.")
        current = root
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    if created_paths is not None:
                        created_paths.append(current / part)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=descriptor)
            stack.callback(os.close, child)
            descriptor = child
            current /= part
        yield descriptor, current, parts[-1]


def _at_open(parent, path, name, flags, mode=0o600):
    return os.open(path / name, flags, mode) if parent is None else os.open(name, flags, mode, dir_fd=parent)


def _at_stat(parent, path, name):
    return (path / name).lstat() if parent is None else os.stat(name, dir_fd=parent, follow_symlinks=False)


def _at_unlink(parent, path, name):
    if parent is None:
        (path / name).unlink()
    else:
        os.unlink(name, dir_fd=parent)


def _at_link(parent, path, source, destination):
    if parent is None:
        os.link(path / source, path / destination, follow_symlinks=False)
    else:
        os.link(source, destination, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)


def _sync_bound(parent, path):
    if parent is None:
        _fsync_parent_directory(path)
    else:
        os.fsync(parent)


@contextmanager
def _duplicate_stream(descriptor, mode):
    """Transfer a duplicate to Python while the original pins cleanup identity."""
    duplicate = os.dup(descriptor)
    try:
        stream = os.fdopen(duplicate, mode)
    except BaseException:
        os.close(duplicate)
        raise
    with stream:
        yield stream


def _publish_new_import_primary(path, data):
    """Small exclusive publication shared by metadata and selected-media import."""
    try:
        _publish_new_import_primary_unchecked(path, data)
    except ValueError as exc:
        # Identity validation can fail after an exclusive link was created.
        # The caller must retain intended ID/hash reconciliation in that case.
        raise OSError("Imported primary publication could not be confirmed") from exc


def _publish_new_import_primary_unchecked(path, data):
    with _bound_parent(path.parent, path.name) as (parent, directory, name):
        if parent is None:
            descriptor, temporary_path = _new_stage(directory)
            temporary = Path(temporary_path).name
        else:
            temporary = ".webjam-import-" + uuid4().hex + ".tmp"
            descriptor = _at_open(parent, directory, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        owned = None
        try:
            owned = os.fstat(descriptor)
            with _duplicate_stream(descriptor, "wb") as writer:
                writer.write(data)
                writer.flush()
                os.fsync(writer.fileno())
            staged = _at_stat(parent, directory, temporary)
            if not stat.S_ISREG(staged.st_mode) or (staged.st_dev, staged.st_ino) != (owned.st_dev, owned.st_ino):
                raise OSError("Prepared import staging changed before publication")
            _at_link(parent, directory, temporary, name)
            current = _at_stat(parent, directory, name)
            if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino):
                raise OSError("Prepared import publication identity is uncertain")
            with _source_bound(parent, directory, name) as (reader, _):
                if reader.read(len(data) + 1) != data:
                    raise OSError("Prepared import published bytes changed")
            _at_unlink(parent, directory, temporary)
            _sync_bound(parent, directory)
        finally:
            try:
                try:
                    remaining = _at_stat(parent, directory, temporary)
                except FileNotFoundError:
                    remaining = None
                if owned is not None and remaining is not None and stat.S_ISREG(remaining.st_mode) and (remaining.st_dev, remaining.st_ino) == (owned.st_dev, owned.st_ino):
                    _at_unlink(parent, directory, temporary)
            finally:
                os.close(descriptor)


def _member(root: Path, name: str) -> Path:
    relative_path(name)
    _directory(root)
    path = root
    for component in name.split("/")[:-1]:
        path /= component
        _directory(path)
    return root.joinpath(*name.split("/"))


@contextmanager
def _source(path: Path, expected=None, *, root=None, relative=None):
    with _bound_parent(root or path.parent, relative or path.name) as (parent, directory, name):
        with _source_bound(parent, directory, name, expected) as source:
            yield source


@contextmanager
def _source_bound(parent, directory, name, expected=None):
    before = _at_stat(parent, directory, name)
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_PACKAGE_BYTES + MAX_PACKAGE_METADATA_BYTES:
        raise WorkspaceBackupError("Media must be a bounded regular file without symbolic links.")
    if expected is not None and (_identity(before), before.st_ctime_ns) != expected:
        raise WorkspaceBackupError("Selected source changed after preview; inspect it again.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = _at_open(parent, directory, name, flags)
    try:
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
                raise WorkspaceBackupError("Media changed while opening.")
            yield handle, before
            after, current = os.fstat(handle.fileno()), _at_stat(parent, directory, name)
            if (not stat.S_ISREG(current.st_mode) or not (_identity(before) == _identity(opened) == _identity(after) == _identity(current))
                    or before.st_ctime_ns != current.st_ctime_ns or opened.st_ctime_ns != after.st_ctime_ns):
                raise WorkspaceBackupError("Media changed while reading; no completed result was published.")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_small(path, maximum, work):
    with _source(path) as (stream, _):
        data = stream.read(maximum + 1)
        work.add(path, len(data))
    if len(data) > maximum:
        raise WorkspaceBackupError("Selected metadata exceeds its bounded size.")
    return data


def _json(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, TypeError, RecursionError) as exc:
        raise WorkspaceBackupError("Package metadata is malformed or unsupported.") from exc


@dataclass(frozen=True)
class _SourceFile:
    root: str
    relative: str
    member: str
    receipt: tuple
    root_identity: tuple
    size_bytes: int
    sha256: str
    role: str

    def proof(self):
        return {"path": self.relative, "size_bytes": self.size_bytes, "sha256": self.sha256, "role": self.role}


def _inspect(root, name, member, role, work, expected_hash=None, expected_size=None):
    root_id = _directory(root)
    path = _member(root, name)
    digestor, size = hashlib.sha256(), 0
    with _source(path, root=root, relative=name) as (stream, before):
        while chunk := stream.read(_CHUNK):
            size += len(chunk)
            if size > MAX_PACKAGE_BYTES:
                raise WorkspaceBackupError("Selected media exceeds its file-size limit.")
            digestor.update(chunk)
            work.add(path, len(chunk))
    actual = digestor.hexdigest()
    if (_directory(root) != root_id or expected_hash is not None and actual != expected_hash
            or expected_size is not None and size != expected_size):
        raise WorkspaceBackupError("Selected media does not match its recorded content identity.")
    return _SourceFile(str(root), name, member, (_identity(before), before.st_ctime_ns), root_id, size, actual, role)


def _take_manifest(root, work):
    from core.take_project import TakeProject
    data = _read_small(_member(root, "webjam-take.json"), MAX_PACKAGE_METADATA_BYTES, work)
    raw = _json(data)
    if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw["schema_version"] != 2:
        raise WorkspaceBackupError("Only completed schema-2 takes support portable media identity; keep this link as metadata.")
    project = TakeProject.from_dict(raw)
    if project.effective_status.value != "complete" or not project.tracks or any(not t.segments for t in project.tracks):
        raise WorkspaceBackupError("Selected take is incomplete or needs attention; keep its original and metadata link.")
    # The consumer parser may normalize malformed fields. Require raw inventory
    # facts explicitly before using them to define a portable package.
    from core.take_library import _schema_v2_segment_shape_valid
    if any(not _schema_v2_segment_shape_valid(s) for t in raw.get("tracks", []) for s in t.get("segments", [])):
        raise WorkspaceBackupError("The completed take has unsupported media facts.")
    return project, data


def _sidecar_data(data, role, project):
    maximum = 64 * 1024 if role == "review" else 8 * 1024**2
    if len(data) > maximum:
        raise WorkspaceBackupError("Saved review or Studio metadata exceeds its size limit.")
    value = _json(data)
    if role == "review":
        if (not isinstance(value, dict) or set(value) != {"schema_version", "take_identity", "favorite", "notes"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["take_identity"] != project.take_id or type(value["favorite"]) is not bool
                or not isinstance(value["notes"], str) or len(value["notes"]) > 8000):
            raise WorkspaceBackupError("Saved review is invalid or belongs to another take.")
        return ()
    from core.studio_store import _decode_document
    document, _, _ = _decode_document(project, data)
    return tuple(sorted({r.source_take_id for r in document.regions if not r.deleted and r.source_take_id != project.take_id}))


def _sidecar(root, name, role, project, work):
    maximum = 64 * 1024 if role == "review" else 8 * 1024**2
    return _sidecar_data(_read_small(_member(root, name), maximum, work), role, project)


@dataclass(frozen=True)
class WorkspacePackagePlan:
    _manifest_bytes: bytes = field(repr=False)
    _sources: tuple[_SourceFile, ...] = field(repr=False)
    blockers: tuple[str, ...] = ()

    @property
    def record(self):
        return decode_session_record(_canonical(_json(self._manifest_bytes)["workspace"]))

    @property
    def included(self):
        return tuple(deepcopy(_json(self._manifest_bytes)["assets"]))

    @property
    def excluded(self):
        return tuple(_json(self._manifest_bytes)["excluded"])

    @property
    def file_count(self):
        return len(self._sources)

    @property
    def total_bytes(self):
        return sum(s.size_bytes for s in self._sources)

    @property
    def exportable(self):
        return not self.blockers


@_domain_errors
def plan_workspace_package(record, selection, *, progress=None, cancel_check=None):
    """Inspect only explicitly selected sources; failed selections remain blockers."""
    if not isinstance(selection, WorkspacePackageSelection):
        raise WorkspaceBackupError("Choose explicit take and Art reference identities.")
    original = WorkspaceBackupPreview(_record_bytes(record))
    record = original.record
    wanted = {*(('take', x) for x in selection.take_ids), *(('art', x) for x in selection.art_reference_ids)}
    work = _Work("Inspect selected media", progress=progress, cancel_check=cancel_check)
    candidates = workspace_backup_candidates(record)
    available = {(c.kind, c.reference_id) for c in candidates}
    blockers = [f"Unknown selected reference: {key}" for key in sorted(wanted - available)]
    sources, assets, excluded = [], [], []
    for candidate in candidates:
        work.check()
        key = candidate.kind, candidate.reference_id
        if key not in wanted:
            excluded.append(f"{candidate.title}: not selected; stored link retained.")
            continue
        try:
            if not candidate.selectable:
                raise WorkspaceBackupError(candidate.reason)
            if candidate.kind == "art":
                source = Path(candidate.locator).expanduser().absolute()
                suffix = source.suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", source.suffix) else ".bin"
                prefix = "art/" + hashlib.sha256(candidate.reference_id.encode()).hexdigest()[:32]
                expected = next((p["files"][0] for p in record.media_provenance if p["kind"] == "art" and p["reference_id"] == candidate.reference_id), None)
                copied = _inspect(source.parent, source.name, prefix + "/content" + suffix, "art", work,
                                  expected["sha256"] if expected else None, expected["size_bytes"] if expected else None)
                # The proof path names the restored member, not its old filename.
                copied = replace(copied, member=prefix + "/content" + suffix)
                proof = copied.proof()
                proof["path"] = "content" + suffix
                asset = {"kind": "art", "reference_id": candidate.reference_id, "prefix": prefix, "files": [proof], "dependencies": []}
                selected_sources = [copied]
            else:
                root = Path(candidate.locator).expanduser().absolute()
                project, data = _take_manifest(root, work)
                if project.take_id != candidate.reference_id:
                    raise WorkspaceBackupError("Selected take ID no longer matches its stored link.")
                checksum = hashlib.sha256(data).hexdigest()
                refs = [r for r in record.take_links if r.get("take_id") == project.take_id]
                if any(r.get("source_identity") and r["source_identity"] != checksum for r in refs):
                    raise WorkspaceBackupError("Selected take manifest changed since it was linked.")
                if len({r.get("take_path", r.get("path", "")) for r in refs}) > 1:
                    raise WorkspaceBackupError("This take ID has ambiguous stored locations.")
                prefix = "takes/" + project.take_id
                selected_sources = [_inspect(root, "webjam-take.json", prefix + "/webjam-take.json", "manifest", work, checksum)]
                seen = {}
                for track in project.tracks:
                    for segment in track.segments:
                        relative_path(segment.path)
                        declaration = segment.sha256, segment.size_bytes
                        if segment.path in seen:
                            if seen[segment.path] != declaration:
                                raise WorkspaceBackupError("Take segments disagree about shared media.")
                            continue
                        seen[segment.path] = declaration
                        selected_sources.append(_inspect(root, segment.path, prefix + "/" + segment.path,
                                                "audio", work, segment.sha256, segment.size_bytes))
                dependencies = set()
                for name, role, _ in _SIDECARS:
                    if role == "review" and not selection.include_reviews or role == "studio" and not selection.include_studio:
                        excluded.append(f"{candidate.title}: {role} metadata explicitly excluded.")
                        continue
                    try:
                        (root / name).lstat()
                    except FileNotFoundError:
                        continue
                    sidecar_data = _read_small(_member(root, name), 64 * 1024 if role == "review" else 8 * 1024**2, work)
                    dependencies.update(_sidecar_data(sidecar_data, role, project))
                    selected_sources.append(_inspect(root, name, prefix + "/" + name, role, work,
                                                     hashlib.sha256(sidecar_data).hexdigest(), len(sidecar_data)))
                # Invoke the actual audio consumer, preserving its format and
                # changed-media policy. Its expensive hash loop is cancellable.
                take = _load_verified_take(root, project.take_id, checksum, work)
                if take.validation_status != "complete":
                    raise WorkspaceBackupError("The selected recording is not complete under current media validation.")
                asset = {"kind": "take", "reference_id": project.take_id, "prefix": prefix,
                         "files": [s.proof() for s in selected_sources], "dependencies": sorted(dependencies)}
            sources.extend(selected_sources)
            assets.append(asset)
        except WorkspacePackageCancelled:
            raise
        except (OSError, ValueError) as exc:
            reason = f"{candidate.title}: {exc}"
            blockers.append(reason)
            excluded.append(reason)
    included_takes = {a["reference_id"] for a in assets if a["kind"] == "take"}
    for asset in assets:
        for dependency in asset["dependencies"]:
            if dependency not in included_takes:
                blockers.append(f"Take {asset['reference_id']} uses Studio source {dependency}; select that take or explicitly exclude Studio edits.")
        if asset["kind"] == "take" and all(d in included_takes for d in asset["dependencies"]):
            try:
                from core.studio_source_catalog import StudioSourceCatalog
                roots = {a["reference_id"]: Path(next(s.root for s in sources if s.member == a["prefix"] + "/webjam-take.json"))
                         for a in assets if a["kind"] == "take"}
                project, _ = _take_manifest(roots[asset["reference_id"]], work)
                StudioSourceCatalog.load(project, roots[asset["reference_id"]], additional_take_roots=[roots[d] for d in asset["dependencies"]])
            except (OSError, ValueError) as exc:
                blockers.append(f"Studio source inventory for {asset['reference_id']} is incompatible: {exc}")
    manifest = {"format": _FORMAT, "version": 1, "workspace": _json(original._payload_bytes),
                "workspace_sha256": original.payload_sha256, "assets": assets, "excluded": excluded}
    payload = _canonical(manifest)
    if (len(payload) + sum(s.size_bytes for s in sources if s.role in {"manifest", "review", "studio"}) > MAX_PACKAGE_METADATA_BYTES
            or len(sources) + 1 > MAX_PACKAGE_FILES or sum(s.size_bytes for s in sources) > MAX_PACKAGE_BYTES):
        blockers.append("Selected package exceeds its bounded inventory or size limits.")
    return WorkspacePackagePlan(payload, tuple(sources), tuple(blockers))


def _inventory(manifest):
    return [{**item, "path": asset["prefix"] + "/" + item["path"]}
            for asset in manifest["assets"] for item in asset["files"]]


def _validate_manifest(data):
    if len(data) > MAX_PACKAGE_METADATA_BYTES:
        raise WorkspaceBackupError("Package manifest exceeds its size limit.")
    value = _json(data)
    if (not isinstance(value, dict) or set(value) != {"format", "version", "workspace", "workspace_sha256", "assets", "excluded"}
            or value["format"] != _FORMAT or type(value["version"]) is not int or value["version"] != 1):
        raise WorkspaceBackupError("Workspace media package format is unsupported.")
    preview = WorkspaceBackupPreview(_canonical(value["workspace"]))
    if preview.payload_sha256 != value["workspace_sha256"]:
        raise WorkspaceBackupError("Workspace payload checksum does not match.")
    if (not isinstance(value["assets"], list) or len(value["assets"]) > MAX_PACKAGE_FILES
            or not isinstance(value["excluded"], list) or any(not isinstance(x, str) or len(x) > 8192 for x in value["excluded"])):
        raise WorkspaceBackupError("Package asset inventory is unsupported.")
    proofs, seen = [], set()
    refs = deepcopy(list(preview.record.take_links))
    for asset in value["assets"]:
        if not isinstance(asset, dict) or set(asset) != {"kind", "reference_id", "prefix", "files", "dependencies"}:
            raise WorkspaceBackupError("Package asset fields are unsupported.")
        if (not isinstance(asset["kind"], str) or asset["kind"] not in {"art", "take"}
                or not isinstance(asset["reference_id"], str) or not asset["reference_id"]
                or len(asset["reference_id"].encode("utf-8")) > 100):
            raise WorkspaceBackupError("Package asset identity is invalid.")
        file_inventory(asset["files"])
        key = asset["kind"], asset["reference_id"]
        active = ({("take", r.get("take_id")) for r in preview.record.take_links}
                  | {("art", r["id"]) for r in preview.record.art.get("references", []) if r["kind"] == "file"})
        if key not in active:
            raise WorkspaceBackupError("Packaged asset has no matching active workspace reference.")
        if key in seen:
            raise WorkspaceBackupError("Package has duplicate reference identities.")
        seen.add(key)
        prefix = relative_path(asset["prefix"])
        expected = ("takes/" + asset["reference_id"] if asset["kind"] == "take"
                    else "art/" + hashlib.sha256(str(asset["reference_id"]).encode()).hexdigest()[:32])
        if prefix != expected:
            raise WorkspaceBackupError("Asset member paths do not match their reference identity.")
        proof = {k: deepcopy(v) for k, v in asset.items() if k != "prefix"}
        proofs.append(proof)
        if asset["kind"] == "take":
            manifests = [f for f in asset["files"] if f.get("role") == "manifest"]
            if len(manifests) != 1:
                raise WorkspaceBackupError("Take manifest inventory is invalid.")
            for ref in refs:
                if ref.get("take_id") == asset["reference_id"]:
                    if ref.get("source_identity") and ref["source_identity"] != manifests[0]["sha256"]:
                        raise WorkspaceBackupError("Take manifest differs from its linked identity.")
                    ref["source_identity"] = manifests[0]["sha256"]
    # Validate relationships with the same pure schema used by local storage.
    encode_session_record(replace(preview.record, take_links=tuple(refs), media_provenance=tuple(proofs)))
    files = _inventory(value)
    if files:
        file_inventory(files)
    if len(files) + 1 > MAX_PACKAGE_FILES or sum(f["size_bytes"] for f in files) + len(data) > MAX_PACKAGE_BYTES:
        raise WorkspaceBackupError("Package exceeds its expanded size or file limit.")
    if len(data) + sum(f["size_bytes"] for f in files if f["role"] in {"manifest", "review", "studio"}) > MAX_PACKAGE_METADATA_BYTES:
        raise WorkspaceBackupError("Package aggregate metadata exceeds its bounded size.")
    take_ids = {a["reference_id"] for a in value["assets"] if a["kind"] == "take"}
    if any(d not in take_ids for a in value["assets"] for d in a["dependencies"]):
        raise WorkspaceBackupError("Package omits a declared Studio dependency.")
    return value


def _zip_info(name):
    info = zipfile.ZipInfo(relative_path(name), (2026, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.compress_type = zipfile.ZIP_STORED
    return info


@dataclass(frozen=True)
class WorkspacePackageReceipt:
    path: Path
    sha256: str
    size_bytes: int
    file_count: int
    publication_receipt: Path | None = None


def _publication_receipt(parent, destination, checksum, size):
    """Durable intended bytes BEFORE publication, discoverable after a crash."""
    path = destination.parent / ("backup-receipt-" + uuid4().hex + ".webjamreceipt")
    data = _canonical({"format": "webjam.backup-publication", "version": 1,
                       "destination": destination.name, "sha256": checksum, "size_bytes": size})
    fd = _at_open(parent, path.parent, path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
    try:
        with _duplicate_stream(fd, "w+b") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_bound(parent, path.parent)
        with _source_bound(parent, path.parent, path.name) as (reader, _):
            if reader.read(len(data) + 1) != data:
                raise WorkspaceBackupError("Backup publication receipt changed; preserve its evidence.")
        info = os.fstat(fd)
        return path, fd, data, (_identity(info), info.st_ctime_ns)
    except BaseException:
        os.close(fd)
        # Never remove an uncertain receipt, even if its own sync failed.
        raise


def _confirm_publication_receipt(parent, path, data, expected):
    with _source_bound(parent, path.parent, path.name, expected) as (reader, _):
        if reader.read(len(data) + 1) != data:
            raise WorkspaceBackupError("Backup publication receipt changed; preserve the package and receipt.")


def _hash_open(stream, work, path):
    hasher = hashlib.sha256()
    count = 0
    while block := stream.read(_CHUNK):
        count += len(block)
        if count > MAX_PACKAGE_BYTES + MAX_PACKAGE_METADATA_BYTES + MAX_PACKAGE_FILES * 4096:
            raise WorkspaceBackupError("Package file exceeds its bounded size.")
        hasher.update(block)
        work.add(path, len(block))
    return hasher.hexdigest(), count


@_domain_errors
def export_workspace_package(plan, destination, *, progress=None, cancel_check=None):
    """Stream an inspected snapshot to a new, exclusively published package."""
    destination = Path(destination).expanduser().absolute()
    with _bound_parent(destination.parent, destination.name) as (parent, directory, name):
        return _export_bound_package(plan, directory / name, parent, progress, cancel_check)


def _export_bound_package(plan, destination, parent, progress, cancel_check):
    if not isinstance(plan, WorkspacePackagePlan) or plan.blockers:
        raise WorkspaceBackupError("Resolve every selected-source blocker before exporting.")
    _validate_manifest(plan._manifest_bytes)
    destination = Path(destination).expanduser().absolute()
    parent_identity = _directory(destination.parent)
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise WorkspaceBackupError("Choose a new backup filename; existing files are never replaced.")
    work = _Work("Copy selected media", plan.total_bytes, progress, cancel_check)
    if parent is None:
        fd, name = _new_stage(destination.parent)
        stage = Path(name)
    else:
        stage = destination.parent / (".webjam-backup-" + uuid4().hex + ".tmp")
        fd = _at_open(parent, destination.parent, stage.name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
    owned = None
    publication_attempted = False
    receipt_path, receipt_fd = None, None
    checksum, size = "", 0
    try:
        with _duplicate_stream(fd, "w+b") as stream:
            owned = os.fstat(stream.fileno())
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                archive.writestr(_zip_info(_MANIFEST), plan._manifest_bytes)
                for source in plan._sources:
                    work.check()
                    root = Path(source.root)
                    if _directory(root) != source.root_identity:
                        raise WorkspaceBackupError("Selected source folder changed after preview.")
                    path = _member(root, source.relative)
                    hasher, size = hashlib.sha256(), 0
                    with _source(path, source.receipt, root=root, relative=source.relative) as (reader, _), archive.open(_zip_info(source.member), "w", force_zip64=True) as writer:
                        while chunk := reader.read(_CHUNK):
                            size += len(chunk)
                            if size > source.size_bytes:
                                raise WorkspaceBackupError("Selected source grew while copying.")
                            writer.write(chunk)
                            hasher.update(chunk)
                            work.add(source.member, len(chunk))
                    if hasher.hexdigest() != source.sha256 or size != source.size_bytes or _directory(root) != source.root_identity:
                        raise WorkspaceBackupError("Selected source changed while copying.")
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            checksum, size = _hash_open(stream, _Work("Verify package", progress=progress, cancel_check=cancel_check), stage)
            work.check()
            if not _same_inode(stage, owned) or _directory(destination.parent) != parent_identity:
                raise WorkspaceBackupError("Backup staging or destination changed before publication.")
            receipt_path, receipt_fd, receipt_bytes, receipt_identity = _publication_receipt(parent, destination, checksum, size)
            work.check()
            if not _same_inode(stage, owned) or _directory(destination.parent) != parent_identity:
                raise WorkspaceBackupError("Backup destination changed before publication; keep its receipt.")
            _confirm_publication_receipt(parent, receipt_path, receipt_bytes, receipt_identity)
            publication_attempted = True
            _at_link(parent, destination.parent, stage.name, destination.name)
            if not _same_inode(destination, owned) or not _same_inode(stage, owned):
                raise WorkspaceBackupError("Published backup identity is uncertain; retain the file and inspect it.")
            _confirm_publication_receipt(parent, receipt_path, receipt_bytes, receipt_identity)
            with _source(destination) as (published, _):
                observed, observed_size = _hash_open(published, _Work("Confirm published backup", progress=progress, cancel_check=cancel_check), destination)
            work.check()
            if observed != checksum or observed_size != size:
                raise WorkspaceBackupError("Published backup bytes changed; its outcome is uncertain. Preserve it before retrying.")
            _confirm_publication_receipt(parent, receipt_path, receipt_bytes, receipt_identity)
            _at_unlink(parent, destination.parent, stage.name)
            _sync_bound(parent, destination.parent)
            _confirm_publication_receipt(parent, receipt_path, receipt_bytes, receipt_identity)
        return WorkspacePackageReceipt(destination, checksum, size, plan.file_count + 1, receipt_path)
    except (OSError, ValueError) as exc:
        if publication_attempted:
            raise WorkspacePackagePublicationUnconfirmed(destination, checksum, size, receipt_path) from exc
        if isinstance(exc, WorkspaceBackupError):
            raise
        raise WorkspaceBackupError("Backup did not reach publication; its source files remain unchanged.") from exc
    finally:
        try:
            if owned is not None and _same_inode(stage, owned):
                _at_unlink(parent, destination.parent, stage.name)
        finally:
            os.close(fd)
            if receipt_fd is not None:
                os.close(receipt_fd)


def _archive_entries(archive):
    infos = archive.infolist()
    if not 1 <= len(infos) <= MAX_PACKAGE_FILES:
        raise WorkspaceBackupError("Package entry count exceeds its limit.")
    names, total = {}, 0
    for info in infos:
        name = relative_path(info.filename)
        if (info.orig_filename != info.filename or name.casefold() in names or info.is_dir()
                or info.external_attr & 0x10 or info.compress_type != zipfile.ZIP_STORED
                or info.flag_bits & 1 or info.file_size != info.compress_size
                or stat.S_IFMT(info.external_attr >> 16) not in {0, stat.S_IFREG}):
            raise WorkspaceBackupError("Package contains an unsupported, linked or duplicate member.")
        names[name.casefold()] = name
        total += info.file_size
        if info.file_size > MAX_PACKAGE_BYTES or total > MAX_PACKAGE_BYTES:
            raise WorkspaceBackupError("Package expanded size exceeds its limit.")
    return {i.filename: i for i in infos}


def _preflight_zip(stream):
    """Bound central-directory allocation BEFORE ZipFile parses its entries."""
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    tail_size = min(size, 65535 + 22)
    stream.seek(size - tail_size)
    tail = stream.read(tail_size)
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or len(tail) - offset < 22:
        raise WorkspaceBackupError("Package ZIP directory is missing.")
    _, disk, cd_disk, disk_entries, count, cd_size, cd_offset, comment = struct.unpack_from("<4s4H2LH", tail, offset)
    eocd = size - tail_size + offset
    if offset + 22 + comment != len(tail) or disk != 0 or cd_disk != 0 or disk_entries != count:
        raise WorkspaceBackupError("Split or trailing ZIP structures are unsupported.")
    end_directory = eocd
    if count == 65535 or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        if eocd < 20:
            raise WorkspaceBackupError("ZIP64 directory locator is missing.")
        stream.seek(eocd - 20)
        locator = stream.read(20)
        signature, start_disk, record_offset, disks = struct.unpack("<4sLQL", locator)
        if signature != b"PK\x06\x07" or start_disk != 0 or disks != 1 or record_offset + 56 != eocd - 20:
            raise WorkspaceBackupError("ZIP64 directory layout is unsupported.")
        stream.seek(record_offset)
        raw = stream.read(56)
        if len(raw) != 56:
            raise WorkspaceBackupError("ZIP64 directory is truncated.")
        signature, record_size, _, _, disk, cd_disk, disk_entries, count, cd_size, cd_offset = struct.unpack("<4sQ2H2L4Q", raw)
        if signature != b"PK\x06\x06" or record_size != 44 or disk or cd_disk or disk_entries != count:
            raise WorkspaceBackupError("ZIP64 directory is unsupported.")
        end_directory = record_offset
    if (not 1 <= count <= MAX_PACKAGE_FILES or cd_size > 8 * 1024**2
            or cd_offset + cd_size != end_directory or cd_offset < 0 or cd_size < count * 46):
        raise WorkspaceBackupError("Package central directory exceeds its bounded layout.")
    stream.seek(cd_offset)
    directory = stream.read(cd_size)
    offset = observed = 0
    while offset < len(directory):
        if len(directory) - offset < 46 or directory[offset:offset + 4] != b"PK\x01\x02":
            raise WorkspaceBackupError("Package central directory records are malformed.")
        name_size, extra_size, comment_size = struct.unpack_from("<3H", directory, offset + 28)
        observed += 1
        if observed > MAX_PACKAGE_FILES or not 1 <= name_size <= 1024 or extra_size > 4096 or comment_size > 1024:
            raise WorkspaceBackupError("Package central directory entry exceeds its limits.")
        offset += 46 + name_size + extra_size + comment_size
    if offset != len(directory) or observed != count:
        raise WorkspaceBackupError("Package central directory count or size does not match its records.")
    stream.seek(0)


@dataclass(frozen=True)
class WorkspacePackagePreview:
    _manifest_bytes: bytes = field(repr=False)
    path: Path
    package_sha256: str
    package_size: int
    _source_receipt: tuple = field(repr=False)

    @property
    def metadata(self):
        return WorkspaceBackupPreview(_canonical(_json(self._manifest_bytes)["workspace"]))

    @property
    def record(self):
        return self.metadata.record

    @property
    def source_id(self):
        return self.metadata.source_id

    @property
    def payload_sha256(self):
        return self.metadata.payload_sha256

    @property
    def included(self):
        return tuple(deepcopy(_json(self._manifest_bytes)["assets"]))

    @property
    def excluded(self):
        return tuple(_json(self._manifest_bytes)["excluded"])

    @property
    def file_count(self):
        return len(_inventory(_json(self._manifest_bytes)))

    @property
    def total_bytes(self):
        return sum(f["size_bytes"] for f in _inventory(_json(self._manifest_bytes)))

    @property
    def limitations(self):
        return ("Only explicitly included files are copied; excluded locations remain stored links.",
                "Original recording and export history stays historical. No playback or external-editor import has occurred.",
                "Private notes and stored paths remain literal; this package does not scrub user-authored secrets.")

    def matching_import_ids(self, library):
        return self.metadata.matching_import_ids(library)


def _read_archive_manifest(archive):
    entries = _archive_entries(archive)
    if _MANIFEST not in entries or entries[_MANIFEST].file_size > MAX_PACKAGE_METADATA_BYTES:
        raise WorkspaceBackupError("Package workspace manifest is missing or too large.")
    data = archive.read(_MANIFEST)
    manifest = _validate_manifest(data)
    if set(entries) != {_MANIFEST, *(f["path"] for f in _inventory(manifest))}:
        raise WorkspaceBackupError("Package members do not exactly match its declared inventory.")
    for item in _inventory(manifest):
        if entries[item["path"]].file_size != item["size_bytes"]:
            raise WorkspaceBackupError("Package member size does not match its inventory.")
    return data, manifest


def _copy_member(archive, item, work, writer=None):
    hasher, count = hashlib.sha256(), 0
    with archive.open(item["path"]) as reader:
        while chunk := reader.read(_CHUNK):
            count += len(chunk)
            if count > item["size_bytes"]:
                raise WorkspaceBackupError("Package member exceeds its declared size.")
            hasher.update(chunk)
            if writer is not None:
                writer.write(chunk)
            work.add(item["path"], len(chunk))
    if count != item["size_bytes"] or hasher.hexdigest() != item["sha256"]:
        raise WorkspaceBackupError("Package member checksum does not match its inventory.")


def _validate_asset_metadata(manifest, metadata):
    """Interpret exact collected bytes; never trust declared dependencies alone."""
    from core.take_project import TakeProject
    projects = {}
    for asset in manifest["assets"]:
        if asset["kind"] != "take":
            continue
        project = TakeProject.from_dict(_json(metadata[asset["prefix"] + "/webjam-take.json"]))
        if project.take_id != asset["reference_id"] or project.effective_status.value != "complete" or not project.tracks:
            raise WorkspaceBackupError("Packaged take manifest is incomplete or has another identity.")
        declared = {s.path: (s.sha256, s.size_bytes) for t in project.tracks for s in t.segments}
        included = {f["path"]: (f["sha256"], f["size_bytes"]) for f in asset["files"] if f["role"] == "audio"}
        if not declared or declared != included:
            raise WorkspaceBackupError("Package audio inventory does not exactly cover its take manifest.")
        dependencies = set()
        for item in asset["files"]:
            if item["role"] in {"review", "studio"}:
                dependencies.update(_sidecar_data(metadata[asset["prefix"] + "/" + item["path"]], item["role"], project))
        if dependencies != set(asset["dependencies"]):
            raise WorkspaceBackupError("Package Studio dependencies differ from the saved arrangement.")
        projects[project.take_id] = project
    for asset in manifest["assets"]:
        if asset["kind"] != "take":
            continue
        project = projects[asset["reference_id"]]
        if any(projects[d].session_id != project.session_id or projects[d].project_sample_rate != project.project_sample_rate for d in asset["dependencies"]):
            raise WorkspaceBackupError("Studio dependencies have incompatible session or sample-rate identities.")


@_domain_errors
def preview_workspace_package(path, *, progress=None, cancel_check=None):
    path = Path(path).expanduser().absolute()
    work = _Work("Verify selected package", progress=progress, cancel_check=cancel_check)
    try:
        with _source(path) as (stream, before):
            checksum, size = _hash_open(stream, work, path)
            _preflight_zip(stream)
            with zipfile.ZipFile(stream) as archive:
                data, manifest = _read_archive_manifest(archive)
                metadata = {}
                for item in _inventory(manifest):
                    collect = item["role"] in {"manifest", "review", "studio"}
                    if collect and item["size_bytes"] > MAX_PACKAGE_METADATA_BYTES:
                        raise WorkspaceBackupError("Packaged take metadata exceeds its limit.")
                    collected = io.BytesIO() if collect else None
                    _copy_member(archive, item, work, collected)
                    if collected is not None:
                        metadata[item["path"]] = collected.getvalue()
                if metadata:
                    _validate_asset_metadata(manifest, metadata)
        return WorkspacePackagePreview(_canonical(manifest), path, checksum, size, (_identity(before), before.st_ctime_ns))
    except (OSError, zipfile.BadZipFile, RuntimeError, KeyError, TypeError, AttributeError, struct.error) as exc:
        raise WorkspaceBackupError("Package could not be verified safely.") from exc


@_domain_errors
def preview_workspace_package_receipt(path, *, progress=None, cancel_check=None):
    """Explicitly check a sibling package against retained publication intent."""
    path = Path(path).expanduser().absolute()
    work = _Work("Check backup receipt", progress=progress, cancel_check=cancel_check)
    value = _json(_read_small(path, 4096, work))
    if (not isinstance(value, dict)
            or set(value) != {"format", "version", "destination", "sha256", "size_bytes"}
            or value["format"] != "webjam.backup-publication"
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["size_bytes"]) is not int
            or not 0 < value["size_bytes"] <= MAX_PACKAGE_BYTES + MAX_PACKAGE_METADATA_BYTES):
        raise WorkspaceBackupError("Backup publication receipt is unsupported.")
    name = relative_path(value["destination"])
    if "/" in name:
        raise WorkspaceBackupError("Backup publication receipt must identify a sibling file.")
    checksum = digest(value["sha256"])
    preview = preview_workspace_package(path.parent / name, progress=progress, cancel_check=cancel_check)
    if preview.package_sha256 != checksum or preview.package_size != value["size_bytes"]:
        raise WorkspaceBackupError("The backup differs from its publication receipt. Keep both files before retrying.")
    return preview


def _load_verified_take(root, take_id, manifest_sha256, work):
    from core.take_library import load_take
    before = _read_small(_member(root, "webjam-take.json"), MAX_PACKAGE_METADATA_BYTES, work)
    if hashlib.sha256(before).hexdigest() != manifest_sha256:
        raise WorkspaceBackupError("Recording manifest differs from the stored identity.")
    take = load_take(root, cancel_check=work.check, progress=work.add)
    after = _read_small(_member(root, "webjam-take.json"), MAX_PACKAGE_METADATA_BYTES, work)
    if (before != after or take is None or take.take_id != take_id or take.manifest_schema_version != 2
            or take.manifest_errors or take.validation_status != "complete" or not take.tracks):
        raise WorkspaceBackupError("Recording is missing, changed or incomplete under current take validation.")
    return take


@dataclass(frozen=True)
class WorkspaceMediaVerification:
    kind: str
    reference_id: str
    locator: str
    sha256: str
    size_bytes: int
    _take: object = field(default=None, repr=False)
    _dependencies: tuple = field(default=(), repr=False)
    _receipts: tuple = field(default=(), repr=False)
    matches_expected_content: bool = True

    @property
    def take(self):
        return deepcopy(self._take)

    @property
    def dependency_takes(self):
        return tuple(deepcopy(take) for take in self._dependencies)

    @property
    def dependency_map(self):
        return {take.take_id: (Path(take.path), deepcopy(take)) for take in self._dependencies}

    @property
    def take_source_identities(self):
        manifests = {source.root: source.sha256 for source in self._receipts if source.role == "manifest"}
        return {take.take_id: manifests[str(take.path)] for take in (self._take, *self._dependencies)
                if take is not None}

    def assert_current(self):
        """Cheap activation gate; actual Studio renderers retain content checks."""
        for source in self._receipts:
            root = Path(source.root)
            path = _member(root, source.relative)
            info = path.lstat()
            if (_directory(root) != source.root_identity or not stat.S_ISREG(info.st_mode)
                    or (_identity(info), info.st_ctime_ns) != source.receipt):
                raise WorkspaceBackupError("Verified media changed before activation; verify it again.")


@_domain_errors
def relink_workspace_media(record, verification):
    """Return a metadata-only edit bound to a current explicit verification."""
    if not isinstance(verification, WorkspaceMediaVerification):
        raise WorkspaceBackupError("Relink requires an explicit media verification result.")
    verification.assert_current()
    edited = replace(decode_session_record(encode_session_record(record)),
                     _store_token=record._store_token, recovered=record.recovered)
    if verification.kind == "art":
        refs = edited.art.get("references", [])
        ref = next((r for r in refs if r["id"] == verification.reference_id and r["kind"] == "file"), None)
        proof = next((p for p in edited.media_provenance if p["kind"] == "art" and p["reference_id"] == verification.reference_id), None)
        if ref is None or proof is None or proof["files"][0]["sha256"] != verification.sha256:
            raise WorkspaceBackupError("Art relink no longer matches the workspace proof.")
        ref["locator"] = verification.locator
    else:
        refs = [r for r in edited.take_links if r.get("take_id") == verification.reference_id]
        if not refs or any(r.get("source_identity") != verification.sha256 for r in refs):
            raise WorkspaceBackupError("Take relink no longer matches the workspace proof.")
        for ref in refs:
            ref["take_path"] = verification.locator
            if "path" in ref:
                ref["path"] = verification.locator
        for song in edited.rehearsal.get("songs", []):
            for mark in song["bookmarks"]:
                if mark.get("take_id") == verification.reference_id and mark.get("source_identity") == verification.sha256:
                    mark["take_path"] = verification.locator
    encode_session_record(edited)
    return edited


@_domain_errors
def verify_workspace_media(record, kind, reference_id, *, locator=None, progress=None, cancel_check=None):
    """Explicitly verify current content and only its declared take dependencies."""
    record = decode_session_record(encode_session_record(record))
    work = _Work("Verify stored media", progress=progress, cancel_check=cancel_check)
    proofs = {(p["kind"], p["reference_id"]): p for p in record.media_provenance}
    proof = proofs.get((kind, reference_id))
    receipts = []
    if kind == "art":
        refs = [r for r in record.art.get("references", []) if r["id"] == reference_id and r["kind"] == "file"]
        if len(refs) != 1:
            raise WorkspaceBackupError("Art reference is not a stored local file.")
        path = Path(locator or refs[0]["locator"]).expanduser().absolute()
        expected = proof["files"][0] if proof else None
        if locator is not None and expected is None:
            raise WorkspaceBackupError("This historical reference has no content checksum; a replacement cannot prove the same artwork.")
        receipt = _inspect(path.parent, path.name, path.name, "art", work,
                           expected["sha256"] if expected else None, expected["size_bytes"] if expected else None)
        return WorkspaceMediaVerification(kind, reference_id, str(path), receipt.sha256, receipt.size_bytes,
                                          _receipts=(receipt,), matches_expected_content=expected is not None)
    if kind != "take":
        raise WorkspaceBackupError("Media reference kind is unsupported.")
    refs = [r for r in record.take_links if r.get("take_id") == reference_id]
    if not refs or len({r.get("take_path", r.get("path", "")) for r in refs}) != 1:
        raise WorkspaceBackupError("Stored take is missing or has ambiguous locations.")
    primary = refs[0]
    path = Path(locator or primary.get("take_path", primary.get("path", ""))).expanduser().absolute()
    checksum = primary.get("source_identity", "")
    digest(checksum)
    takes = []
    # Current saved edits can legitimately remove or add lanes after restore.
    # Their durable IDs may resolve only through explicit workspace references,
    # never a parent/global take scan or arbitrary paths from the Studio file.
    from core.studio_store import load_studio_document
    document = load_studio_document(path).document
    dependencies = sorted({r.source_take_id for r in document.regions if not r.deleted and r.source_take_id != reference_id})
    requested = [reference_id, *dependencies]
    for key in requested:
        linked = [r for r in record.take_links if r.get("take_id") == key]
        if not linked or len({r.get("take_path", r.get("path", "")) for r in linked}) != 1:
            raise WorkspaceBackupError("A declared Studio dependency is absent from this workspace.")
        current = linked[0]
        root = path if key == reference_id else Path(current.get("take_path", "")).expanduser().absolute()
        media_proof = proofs.get(("take", key))
        if media_proof:
            for item in media_proof["files"]:
                if item["role"] in {"manifest", "audio"}:
                    receipts.append(_inspect(root, item["path"], item["path"], item["role"], work, item["sha256"], item["size_bytes"]))
        else:
            project, data = _take_manifest(root, work)
            receipts.append(_inspect(root, "webjam-take.json", "webjam-take.json", "manifest", work, current.get("source_identity")))
            for track in project.tracks:
                for segment in track.segments:
                    receipts.append(_inspect(root, segment.path, segment.path, "audio", work, segment.sha256, segment.size_bytes))
        takes.append(_load_verified_take(root, key, current.get("source_identity", ""), work))
    from core.studio_source_catalog import StudioSourceCatalog
    project, _ = _take_manifest(path, work)
    StudioSourceCatalog.load(project, path, additional_take_roots=[t.path for t in takes[1:]])
    result = WorkspaceMediaVerification(kind, reference_id, str(path), checksum,
                                        sum(r.size_bytes for r in receipts), takes[0], tuple(takes[1:]), tuple(receipts))
    result.assert_current()
    return result


def _journal_bytes(record, context):
    value = {"format": "webjam.media-import", "version": 1, "record": encode_session_record(record).decode("utf-8"),
             "record_sha256": hashlib.sha256(encode_session_record(record)).hexdigest(), "media": context}
    data = _canonical(value)
    if len(data) > 24 * 1024**2:
        raise WorkspaceBackupError("Media import recovery exceeds its bounded size.")
    decode_media_import_journal(data)
    return data


@_domain_errors
def decode_media_import_journal(data):
    """Pure bounded metadata decode shared with SessionLibrary recovery listing."""
    if len(data) > 24 * 1024**2:
        raise WorkspaceBackupError("Media import journal is too large.")
    value = _json(data)
    if (not isinstance(value, dict) or set(value) != {"format", "version", "record", "record_sha256", "media"}
            or value["format"] != "webjam.media-import" or type(value["version"]) is not int or value["version"] != 1):
        raise WorkspaceBackupError("Media import journal schema is invalid.")
    if not isinstance(value["record"], str):
        raise WorkspaceBackupError("Media journal must retain exact prepared record bytes.")
    record = decode_session_record(value["record"].encode("utf-8"))
    encoded = SessionLibrary._prepared_import_bytes(record)
    stored = value["record"].encode("utf-8")
    if stored != encoded or hashlib.sha256(stored).hexdigest() != value["record_sha256"]:
        raise WorkspaceBackupError("Prepared import record checksum differs from its recovery journal.")
    context = value["media"]
    if (not isinstance(context, dict) or set(context) != {"restore_id", "root", "package_sha256", "manifest_sha256", "files", "references", "phase"}
            or not isinstance(context["restore_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", context["restore_id"])
            or context["root"] != "media/" + context["restore_id"] or context["phase"] not in {"prepared", "complete"}):
        raise WorkspaceBackupError("Media import ownership context is invalid.")
    digest(context["package_sha256"])
    digest(context["manifest_sha256"])
    if not isinstance(context["files"], list):
        raise WorkspaceBackupError("Media recovery files must be a list.")
    if context["files"]:
        file_inventory(context["files"])
    selected = context["references"]
    if (not isinstance(selected, list) or len(selected) > MAX_PACKAGE_FILES
            or any(not isinstance(k, list) or len(k) != 2 or any(not isinstance(v, str) for v in k) for k in selected)
            or len({tuple(k) for k in selected}) != len(selected)):
        raise WorkspaceBackupError("Media recovery reference selection is invalid.")
    known = {(p["kind"], p["reference_id"]) for p in record.media_provenance}
    if not {tuple(k) for k in selected} <= known:
        raise WorkspaceBackupError("Media recovery selection has no matching workspace proof.")
    # Every proof must map to exactly one declared restored file; no extra data.
    expected = []
    for proof in record.media_provenance:
        if [proof["kind"], proof["reference_id"]] not in selected:
            continue
        prefix = ("takes/" + proof["reference_id"] if proof["kind"] == "take"
                  else "art/" + hashlib.sha256(proof["reference_id"].encode()).hexdigest()[:32])
        expected.extend({**f, "path": prefix + "/" + f["path"]} for f in proof["files"])
    if sorted(expected, key=lambda f: f["path"]) != sorted(context["files"], key=lambda f: f["path"]):
        raise WorkspaceBackupError("Media recovery inventory does not match prepared workspace proofs.")
    return record, deepcopy(context)


def same_media_import_transaction(retained, current):
    """Allow only the explicit prepared→complete transition of one intent."""
    old_record, old = decode_media_import_journal(retained)
    new_record, new = decode_media_import_journal(current)
    old_phase, new_phase = old.pop("phase"), new.pop("phase")
    return (encode_session_record(old_record) == encode_session_record(new_record) and old == new
            and (old_phase == new_phase or (old_phase, new_phase) == ("prepared", "complete")))


def _owner_bytes(record, context):
    return _canonical({"restore_id": context["restore_id"], "record_sha256": hashlib.sha256(encode_session_record(record)).hexdigest(),
                       "inventory_sha256": hashlib.sha256(_canonical(context["files"])).hexdigest(),
                       "package_sha256": context["package_sha256"]})


@_domain_errors
def prepare_workspace_package_import(library, preview):
    if not isinstance(preview, WorkspacePackagePreview):
        raise WorkspaceBackupError("Import requires a verified package preview.")
    manifest = _validate_manifest(preview._manifest_bytes)
    record = prepare_workspace_backup_import(preview.metadata)
    restore_id = uuid4().hex
    root = library.root.absolute() / "media" / restore_id
    refs, art, rehearsal = deepcopy(list(record.take_links)), deepcopy(record.art), deepcopy(record.rehearsal)
    proofs = []
    for asset in manifest["assets"]:
        proof = {k: deepcopy(v) for k, v in asset.items() if k != "prefix"}
        proofs.append(proof)
        path = root.joinpath(*asset["prefix"].split("/"))
        if asset["kind"] == "take":
            checksum = next(f["sha256"] for f in asset["files"] if f["role"] == "manifest")
            for ref in refs:
                if ref.get("take_id") == asset["reference_id"]:
                    ref["take_path"], ref["source_identity"] = str(path), checksum
                    if "path" in ref:
                        ref["path"] = str(path)
            for song in rehearsal.get("songs", []):
                for mark in song["bookmarks"]:
                    if mark.get("take_id") == asset["reference_id"] and mark.get("source_identity") == checksum:
                        mark["take_path"] = str(path)
        else:
            for ref in art.get("references", []):
                if ref["id"] == asset["reference_id"]:
                    ref["locator"] = str(path / asset["files"][0]["path"])
    # Selected assets replace matching old proofs. Unselected proofs remain in
    # the portable source payload, but this transaction journals only new files.
    selected = {(p["kind"], p["reference_id"]) for p in proofs}
    retained = [p for p in record.media_provenance if (p["kind"], p["reference_id"]) not in selected]
    record = replace(record, take_links=tuple(refs), art=art, rehearsal=rehearsal, media_provenance=tuple([*retained, *proofs]))
    context = {"restore_id": restore_id, "root": "media/" + restore_id,
               "package_sha256": preview.package_sha256, "manifest_sha256": hashlib.sha256(preview._manifest_bytes).hexdigest(),
               "files": _inventory(manifest), "references": [[p["kind"], p["reference_id"]] for p in proofs], "phase": "prepared"}
    data = _journal_bytes(record, context)
    with library._locked():
        if library._pending_import() is not None:
            raise SessionLibraryConflict("Check the previous import before starting another one.")
        if library._reconcile_import(record) is not None:
            raise SessionLibraryConflict("Prepared workspace identity already exists.")
        library._unconfirmed_import = decode_session_record(encode_session_record(record))
        library._unconfirmed_media_import = data
        try:
            atomic_write_bytes(library.root / ".workspace-import.pending", data, mode=0o600)
        except OSError as exc:
            raise SessionLibraryImportUnconfirmed(record.id, hashlib.sha256(encode_session_record(record)).hexdigest(), phase="journal") from exc
    return record


def _pending(library):
    with library._locked():
        path = library.root / ".workspace-import.pending"
        data = _read(path)
        fallback = library._unconfirmed_media_import
        if data is not None and fallback is not None and not same_media_import_transaction(fallback, data):
            raise SessionLibraryConflict("Pending package context differs from its retained transaction; preserve both before retrying.")
        if data is None:
            completed_path = library.root / ".workspace-import.completed"
            completed = _read(completed_path)
            if (completed is not None and library._is_media_journal(completed)
                    and (fallback is None or same_media_import_transaction(fallback, completed))):
                path, data = completed_path, completed
            elif (completed is not None and fallback is not None and library._is_media_journal(completed)
                  and library._journal_record(completed).id == library._journal_record(fallback).id):
                raise SessionLibraryConflict("Completed package context differs from the intended transaction; preserve both receipts.")
            elif fallback is not None:
                data = fallback
        if data is None or not library._is_media_journal(data):
            raise WorkspaceBackupError("No package import recovery receipt is available.")
        record, context = decode_media_import_journal(data)
        return data, record, context, path


def _restore_missing_journal(library, data, record, path):
    """An explicit retry may finish the original failed preparation only."""
    with library._locked():
        current = _read(path)
        if current is not None and current != data:
            raise SessionLibraryConflict("Package recovery changed before retry.")
        if current is None:
            if library._unconfirmed_media_import != data:
                raise SessionLibraryConflict("Package preparation has no retained exact recovery context.")
            try:
                atomic_write_bytes(path, data, mode=0o600)
            except OSError as exc:
                raise SessionLibraryImportUnconfirmed(record.id, hashlib.sha256(encode_session_record(record)).hexdigest(), phase="journal") from exc


def _root_for(library, record, context):
    root = library.root.absolute().joinpath(*context["root"].split("/"))
    for proof in record.media_provenance:
        if [proof["kind"], proof["reference_id"]] not in context["references"]:
            continue
        prefix = ("takes/" + proof["reference_id"] if proof["kind"] == "take"
                  else "art/" + hashlib.sha256(proof["reference_id"].encode()).hexdigest()[:32])
        expected = root.joinpath(*prefix.split("/"))
        if proof["kind"] == "take":
            refs = [r for r in record.take_links if r.get("take_id") == proof["reference_id"]]
            if any(r.get("take_path") != str(expected) for r in refs):
                raise WorkspaceBackupError("Prepared recording path differs from its owned media root.")
        else:
            ref = next(r for r in record.art["references"] if r["id"] == proof["reference_id"])
            if ref["locator"] != str(expected / proof["files"][0]["path"]):
                raise WorkspaceBackupError("Prepared Art path differs from its owned media root.")
    return root


def _verify_restored(library, record, context, work):
    root = _root_for(library, record, context)
    _directory(root.parent)
    root_id = _directory(root)
    owner = _read_small(root / _OWNER, 4096, work)
    if owner != _owner_bytes(record, context):
        raise WorkspaceBackupError("Restored media ownership marker does not match this transaction.")
    _verify_owned_tree(root, context["files"], work)
    owner_receipt = _inspect(root, _OWNER, _OWNER, "owner", work, hashlib.sha256(owner).hexdigest(), len(owner))
    receipts = (owner_receipt, *( _inspect(root, f["path"], f["path"], f["role"], work, f["sha256"], f["size_bytes"]) for f in context["files"]))
    # Actual consumers must accept the restored complete take and sidecars.
    for proof in record.media_provenance:
        if [proof["kind"], proof["reference_id"]] not in context["references"]:
            continue
        if proof["kind"] == "take":
            take_root = root / "takes" / proof["reference_id"]
            project, data = _take_manifest(take_root, work)
            _load_verified_take(take_root, proof["reference_id"], hashlib.sha256(data).hexdigest(), work)
            dependencies = set()
            for item in proof["files"]:
                if item["role"] in {"review", "studio"}:
                    dependencies.update(_sidecar(take_root, item["path"], item["role"], project, work))
            if dependencies != set(proof["dependencies"]):
                raise WorkspaceBackupError("Restored Studio dependencies differ from the saved edits.")
            from core.studio_source_catalog import StudioSourceCatalog
            StudioSourceCatalog.load(project, take_root, additional_take_roots=[root / "takes" / d for d in proof["dependencies"]])
    return root, root_id, receipts


def _verify_owned_tree(root, files, work):
    expected = {_OWNER, *(f["path"] for f in files)}
    parents = {""}
    for name in expected:
        parts = name.split("/")
        parents.update("/".join(parts[:i]) for i in range(1, len(parts)))
    pending, count = [""], 0
    while pending:
        directory = pending.pop()
        with _bound_parent(root, (directory + "/" if directory else "") + "placeholder") as (descriptor, path, _):
            with os.scandir(descriptor if descriptor is not None else path) as entries:
                for entry in entries:
                    work.check()
                    count += 1
                    if count > MAX_PACKAGE_FILES * 17:
                        raise WorkspaceBackupError("Restored directory inventory exceeds its limit.")
                    relative = (directory + "/" if directory else "") + entry.name
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                        if relative not in parents:
                            raise WorkspaceBackupError("Restored root contains an undeclared directory; preserve it before retrying.")
                        pending.append(relative)
                    elif stat.S_ISREG(info.st_mode):
                        abandoned_stage = re.fullmatch(r"\.webjam-(?:part-[0-9a-f]{32}|backup-[0-9a-f]{32}\.tmp)", entry.name)
                        if relative not in expected and not abandoned_stage:
                            raise WorkspaceBackupError("Restored root contains an undeclared file; preserve it before retrying.")
                    else:
                        raise WorkspaceBackupError("Restored root contains a linked or special file; preserve it before retrying.")


def _require_receipts(root, root_id, receipts):
    if _directory(root) != root_id:
        raise WorkspaceBackupError("Restored media root changed before publication.")
    for source in receipts:
        path = _member(Path(source.root), source.relative)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or (_identity(info), info.st_ctime_ns) != source.receipt:
            raise WorkspaceBackupError("Restored media changed after verification.")


@dataclass(frozen=True)
class WorkspacePackageRecovery:
    record: SessionRecord
    state: str
    can_retry: bool
    detail: str


@_domain_errors
def reconcile_workspace_package_import(library, *, retry=False, expected_record=None, progress=None, cancel_check=None):
    """Check without writes by default; explicit retry publishes the SAME ID."""
    journal, record, context, journal_path = _pending(library)
    if expected_record is not None and encode_session_record(expected_record) != encode_session_record(record):
        raise SessionLibraryConflict("The intended import changed. Check its current recovery evidence before retrying.")
    if retry:
        _restore_missing_journal(library, journal, record, journal_path)
    work = _Work("Check restored package", sum(f["size_bytes"] for f in context["files"]), progress, cancel_check)
    try:
        root, root_id, receipts = _verify_restored(library, record, context, work)
    except (OSError, ValueError) as exc:
        if isinstance(exc, WorkspacePackageCancelled):
            raise
        if retry:
            raise WorkspaceBackupError("Restore is incomplete or changed. Retry with the same verified package to resume extraction.") from exc
        return WorkspacePackageRecovery(record, "partial", False, str(exc))
    work.check()
    with library._locked():
        if _read(journal_path) != journal:
            raise SessionLibraryConflict("Pending package evidence changed while checking.")
        _require_receipts(root, root_id, receipts)
        saved = library._reconcile_import(record)
        completed_receipt = journal_path.name == ".workspace-import.completed"
        if completed_receipt and saved is None:
            raise SessionLibraryConflict("Completed import receipt has no matching workspace; preserve its evidence.")
        if not retry:
            return WorkspacePackageRecovery(saved or record, "published" if saved else "ready", True,
                                             "Exact workspace and restored media verified." if saved else "Restored media verified; the workspace has not been published.")
        context["phase"] = "complete"
        complete = _journal_bytes(record, context)
        try:
            if not completed_receipt:
                atomic_write_bytes(library.root / ".workspace-import.pending", complete, mode=0o600)
            if saved is None:
                encoded = encode_session_record(record)
                library._publish_import_file(library._path(record.id), encoded)
                saved = replace(record, _store_token=hashlib.sha256(encoded).hexdigest())
            if not completed_receipt:
                previous = _read(library.root / ".workspace-import.completed")
                if previous is not None:
                    library._prepared_import_bytes(library._journal_record(previous))
                os.replace(library.root / ".workspace-import.pending", library.root / ".workspace-import.completed")
            _fsync_parent_directory(library.root)
        except OSError as exc:
            raise SessionLibraryImportUnconfirmed(record.id, hashlib.sha256(encode_session_record(record)).hexdigest()) from exc
        library._unconfirmed_import = None
        library._unconfirmed_media_import = None
        return saved


def _extract(library, preview, journal, record, context, work):
    root = _root_for(library, record, context)
    # Preparation is durable before any media directories or files are created.
    marker = _owner_bytes(record, context)
    created_paths = []
    with _bound_parent(library.root, context["root"] + "/" + _OWNER, create=True, created_paths=created_paths) as (parent, directory, name):
        if root in created_paths:
            fd = _at_open(parent, directory, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
            with os.fdopen(fd, "wb") as writer:
                writer.write(marker)
                writer.flush()
                os.fsync(writer.fileno())
            _sync_bound(parent, directory)
        elif _read_small(root / _OWNER, 4096, work) != marker:
            raise WorkspaceBackupError("Restore root belongs to different or unresolved work; it was preserved.")
    with _source(preview.path, preview._source_receipt) as (stream, _):
        checksum, size = _hash_open(stream, work, preview.path)
        if checksum != preview.package_sha256 or size != preview.package_size or checksum != context["package_sha256"]:
            raise WorkspaceBackupError("Selected package changed since preview.")
        _preflight_zip(stream)
        with zipfile.ZipFile(stream) as archive:
            _, manifest = _read_archive_manifest(archive)
            if _canonical(manifest) != preview._manifest_bytes:
                raise WorkspaceBackupError("Selected package manifest changed since preview.")
            for item in context["files"]:
                work.check()
                with _bound_parent(root, item["path"], create=True) as (parent, directory, final_name):
                    try:
                        _at_stat(parent, directory, final_name)
                    except FileNotFoundError:
                        pass
                    else:
                        # Explicit resume accepts only exact completed files.
                        _inspect(root, item["path"], item["path"], item["role"], work, item["sha256"], item["size_bytes"])
                        continue
                    temporary = ".webjam-part-" + uuid4().hex
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                    if os.name == "nt":
                        fd, temporary_path = _new_stage(directory)
                        temporary = Path(temporary_path).name
                    else:
                        fd = _at_open(parent, directory, temporary, flags)
                    owned = None
                    try:
                        owned = os.fstat(fd)
                        with _duplicate_stream(fd, "wb") as writer:
                            _copy_member(archive, item, work, writer)
                            writer.flush()
                            os.fsync(writer.fileno())
                            staged = _at_stat(parent, directory, temporary)
                            if not stat.S_ISREG(staged.st_mode) or (staged.st_dev, staged.st_ino) != (owned.st_dev, owned.st_ino):
                                raise WorkspaceBackupError("Restored member staging changed before publication.")
                            _at_link(parent, directory, temporary, final_name)
                            published = _at_stat(parent, directory, final_name)
                            if not stat.S_ISREG(published.st_mode) or (published.st_dev, published.st_ino) != (owned.st_dev, owned.st_ino):
                                raise WorkspaceBackupError("Restored member publication identity is uncertain.")
                            _at_unlink(parent, directory, temporary)
                            _sync_bound(parent, directory)
                    finally:
                        try:
                            try:
                                retained = _at_stat(parent, directory, temporary)
                            except FileNotFoundError:
                                retained = None
                            if owned is not None and retained is not None and stat.S_ISREG(retained.st_mode) and (retained.st_dev, retained.st_ino) == (owned.st_dev, owned.st_ino):
                                _at_unlink(parent, directory, temporary)
                        finally:
                            os.close(fd)
    _fsync_parent_directory(root)
    with library._locked():
        if _read(library.root / ".workspace-import.pending") != journal:
            raise SessionLibraryConflict("Pending package changed during extraction; its files were retained.")


@_domain_errors
def import_workspace_package(library, preview, *, retry=False, expected_record=None, progress=None, cancel_check=None):
    """Restore selected bytes and publish once; explicit retries keep one ID."""
    if not isinstance(preview, WorkspacePackagePreview):
        raise WorkspaceBackupError("Import requires a verified package preview.")
    if not retry:
        expected_record = prepare_workspace_package_import(library, preview)
    journal, record, context, journal_path = _pending(library)
    if expected_record is not None and encode_session_record(expected_record) != encode_session_record(record):
        raise SessionLibraryConflict("The intended import changed. Check its current recovery evidence before retrying.")
    if (context["package_sha256"] != preview.package_sha256
            or context["manifest_sha256"] != hashlib.sha256(preview._manifest_bytes).hexdigest()):
        raise WorkspaceBackupError("Resume requires the exact original package preview.")
    work = _Work("Restore selected files", preview.total_bytes, progress, cancel_check)
    try:
        if retry:
            _restore_missing_journal(library, journal, record, journal_path)
        if journal_path.name == ".workspace-import.pending":
            _extract(library, preview, journal, record, context, work)
        return reconcile_workspace_package_import(library, retry=True, expected_record=record,
                                                   progress=progress, cancel_check=cancel_check)
    except (OSError, zipfile.BadZipFile, RuntimeError, KeyError) as exc:
        raise WorkspaceBackupError("Package restore did not finish. Its exact recovery evidence and owned files were retained.") from exc
