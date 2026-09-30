"""Static, versioned role -> permission map (ADR-0008).

Roles come from server-side memberships and platform roles, never from token claims.
"""

from enum import StrEnum

PERMISSIONS_VERSION = 1


class Permission(StrEnum):
    BOOKING_READ = "booking.read"
    BOOKING_WRITE = "booking.write"
    CATALOG_READ = "catalog.read"
    CATALOG_MANAGE = "catalog.manage"
    STAFF_READ = "staff.read"
    STAFF_MANAGE = "staff.manage"
    MEMBERS_MANAGE = "members.manage"
    MEMBERS_MANAGE_ADMINS = "members.manage_admins"
    SETTINGS_MANAGE = "settings.manage"
    READINESS_READ = "readiness.read"
    PLATFORM_TENANT_STATUS = "platform.tenant_status"
    PLATFORM_GO_LIVE = "platform.go_live"


_STAFF = frozenset({Permission.BOOKING_READ, Permission.CATALOG_READ, Permission.STAFF_READ})
_FRONT_DESK = _STAFF | {Permission.BOOKING_WRITE}
_MANAGER = _FRONT_DESK | {
    Permission.CATALOG_MANAGE,
    Permission.STAFF_MANAGE,
    Permission.MEMBERS_MANAGE,
    Permission.SETTINGS_MANAGE,
    Permission.READINESS_READ,
}
_OWNER = _MANAGER | {Permission.MEMBERS_MANAGE_ADMINS}

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "artist": _STAFF,
    "front_desk": _FRONT_DESK,
    "manager": _MANAGER,
    "owner": _OWNER,
}

ADMIN_ROLES = frozenset({"owner", "manager"})

# What a platform admin may do inside a salon it is not a member of: read-only support.
PLATFORM_SUPPORT_PERMISSIONS = frozenset(
    {
        Permission.BOOKING_READ,
        Permission.CATALOG_READ,
        Permission.STAFF_READ,
        Permission.READINESS_READ,
    }
)
PLATFORM_ADMIN_PERMISSIONS = PLATFORM_SUPPORT_PERMISSIONS | {
    Permission.PLATFORM_TENANT_STATUS,
    Permission.PLATFORM_GO_LIVE,
}


def is_salon_admin(role: str) -> bool:
    return role in ADMIN_ROLES
