"""Classify a normalized local volume before accessing resource metadata."""
import os


def is_fixed_local_volume(anchor: str) -> bool:
    """Reject mapped network and removable Windows volumes."""
    if os.name != "nt":
        return True
    from ctypes import windll

    return windll.kernel32.GetDriveTypeW(anchor) == 3  # DRIVE_FIXED
