from gorgona_booking.auth.permissions import (
    PLATFORM_ADMIN_PERMISSIONS,
    PLATFORM_SUPPORT_PERMISSIONS,
    ROLE_PERMISSIONS,
    Permission,
    is_salon_admin,
)


def test_roles_form_a_strict_hierarchy() -> None:
    artist, desk, manager, owner = (
        ROLE_PERMISSIONS[r] for r in ("artist", "front_desk", "manager", "owner")
    )
    assert artist < desk < manager < owner


def test_staff_cannot_manage_and_admins_can() -> None:
    manage = {Permission.CATALOG_MANAGE, Permission.STAFF_MANAGE, Permission.MEMBERS_MANAGE}
    for role in ("artist", "front_desk"):
        assert not manage & ROLE_PERMISSIONS[role]
        assert not is_salon_admin(role)
    for role in ("manager", "owner"):
        assert manage <= ROLE_PERMISSIONS[role]
        assert is_salon_admin(role)
    assert Permission.MEMBERS_MANAGE_ADMINS in ROLE_PERMISSIONS["owner"]
    assert Permission.MEMBERS_MANAGE_ADMINS not in ROLE_PERMISSIONS["manager"]


def test_no_salon_role_holds_a_platform_permission() -> None:
    platform_only = PLATFORM_ADMIN_PERMISSIONS - PLATFORM_SUPPORT_PERMISSIONS
    assert platform_only
    for permissions in ROLE_PERMISSIONS.values():
        assert not permissions & platform_only


def test_platform_support_access_is_read_only() -> None:
    writes = {
        Permission.BOOKING_WRITE,
        Permission.CATALOG_MANAGE,
        Permission.STAFF_MANAGE,
        Permission.MEMBERS_MANAGE,
        Permission.SETTINGS_MANAGE,
    }
    assert not PLATFORM_SUPPORT_PERMISSIONS & writes
    assert PLATFORM_SUPPORT_PERMISSIONS <= PLATFORM_ADMIN_PERMISSIONS
