"""Map a verified token to a known, active user (no automatic account creation)."""

from dataclasses import dataclass
from uuid import UUID

from gorgona_booking.auth.verifier import VerifiedToken
from gorgona_booking.db.pool import RuntimeConnection, RuntimePool
from gorgona_booking.errors import DomainError


class IdentityNotLinkedError(DomainError):
    code = "IDENTITY_NOT_LINKED"


class UserDisabledError(DomainError):
    code = "USER_DISABLED"


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    display_name: str
    platform_roles: frozenset[str]

    @property
    def is_platform_admin(self) -> bool:
        return "platform_admin" in self.platform_roles

    @property
    def actor(self) -> str:
        return f"user:{self.user_id}"


async def set_identity_context(conn: RuntimeConnection, token: VerifiedToken) -> None:
    await conn.execute(
        "select pg_catalog.set_config('gba.auth_issuer', %s, true), "
        "pg_catalog.set_config('gba.auth_subject', %s, true)",
        (token.issuer, token.subject),
    )


async def set_user_context(
    conn: RuntimeConnection, user_id: UUID, *, request_id: str | None = None
) -> None:
    await conn.execute(
        "select pg_catalog.set_config('gba.user_id', %s, true), "
        "pg_catalog.set_config('gba.actor', %s, true), "
        "pg_catalog.set_config('gba.request_id', %s, true)",
        (str(user_id), f"user:{user_id}", request_id or ""),
    )


async def linked_user_id(conn: RuntimeConnection, token: VerifiedToken) -> UUID | None:
    """The user linked to this token's (issuer, subject); RLS shows no other row."""
    await set_identity_context(conn, token)
    row = await (
        await conn.execute(
            "select user_id from gba.user_identities where issuer = %s and subject = %s",
            (token.issuer, token.subject),
        )
    ).fetchone()
    return UUID(str(row[0])) if row else None


async def resolve_principal(pool: RuntimePool, token: VerifiedToken) -> Principal:
    async with pool.connection() as conn, conn.transaction():
        user_id = await linked_user_id(conn, token)
        if user_id is None:
            raise IdentityNotLinkedError("This sign-in is not linked to an account")
        await set_user_context(conn, user_id)
        user = await (
            await conn.execute(
                "select status, display_name from gba.users where id = %s", (user_id,)
            )
        ).fetchone()
        if user is None or user[0] != "active":
            raise UserDisabledError("This account is disabled")
        roles = await (
            await conn.execute(
                "select role from gba.platform_roles where user_id = %s and revoked_at is null",
                (user_id,),
            )
        ).fetchall()
    return Principal(
        user_id=user_id, display_name=str(user[1]), platform_roles=frozenset(r[0] for r in roles)
    )
