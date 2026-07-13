"""Small, dependency-free platform permission probes used by the UI.

The probe never requests access and never blocks.  It lets WebJam explain a
known macOS denial before starting the external music process.  Unknown
platforms and failed probes return ``unavailable`` so they never create a
false blocker.
"""

from __future__ import annotations

import ctypes
import logging
import sys


LOGGER = logging.getLogger("webjam.qt.permissions")


def microphone_permission_status() -> str:
    """Return authorized, not_determined, denied, restricted, or unavailable."""
    if sys.platform != "darwin":
        return "unavailable"
    try:
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
        ctypes.CDLL(
            "/System/Library/Frameworks/AVFoundation.framework/AVFoundation"
        )

        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        message_address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        if not message_address:
            return "unavailable"

        send_id = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_char_p,
        )(message_address)
        send_integer = ctypes.CFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(message_address)

        string_class = objc.objc_getClass(b"NSString")
        make_string = objc.sel_registerName(b"stringWithUTF8String:")
        audio_media_type = send_id(string_class, make_string, b"soun")
        capture_device = objc.objc_getClass(b"AVCaptureDevice")
        authorization = objc.sel_registerName(b"authorizationStatusForMediaType:")
        raw_status = int(send_integer(capture_device, authorization, audio_media_type))
        return {
            0: "not_determined",
            1: "restricted",
            2: "denied",
            3: "authorized",
        }.get(raw_status, "unavailable")
    except (AttributeError, OSError, TypeError, ValueError):
        LOGGER.debug("Could not query macOS microphone permission", exc_info=True)
        return "unavailable"
