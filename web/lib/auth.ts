import {
  InMemoryWebStorage,
  UserManager,
  WebStorageStateStore,
} from "oidc-client-ts";
import { z } from "zod";

const configSchema = z.object({
  authority: z.string().url(),
  client_id: z.string().min(1).max(255),
  scope: z.string().min(1).max(1024).default("openid profile email"),
  resource: z.string().max(1024).optional(),
});
export type AuthConfig = z.infer<typeof configSchema>;
let managerPromise: Promise<UserManager | null> | null = null;
let callbackPromise: Promise<void> | null = null;

function validateConfig(value: unknown): AuthConfig | null {
  const parsed = configSchema.safeParse(value);
  if (!parsed.success) return null;
  const authority = new URL(parsed.data.authority);
  const local = ["localhost", "127.0.0.1", "[::1]"];
  const localTest =
    local.includes(window.location.hostname) &&
    local.includes(authority.hostname);
  if (
    (authority.protocol !== "https:" &&
      !(localTest && authority.protocol === "http:")) ||
    authority.username ||
    authority.password ||
    authority.search ||
    authority.hash ||
    !parsed.data.scope.split(/\s+/).includes("openid") ||
    parsed.data.scope.split(/\s+/).includes("offline_access")
  )
    return null;
  return parsed.data;
}

export function authManager(): Promise<UserManager | null> {
  if (typeof window === "undefined") return Promise.resolve(null);
  managerPromise ??= (async () => {
    // Remove credentials left by the obsolete developer-only token form.
    localStorage.removeItem("gorgona_staff_token");
    let config = validateConfig({
      authority: process.env.NEXT_PUBLIC_OIDC_AUTHORITY,
      client_id: process.env.NEXT_PUBLIC_OIDC_CLIENT_ID,
      scope: process.env.NEXT_PUBLIC_OIDC_SCOPE ?? "openid profile email",
      resource: process.env.NEXT_PUBLIC_OIDC_RESOURCE,
    });
    if (!config) {
      try {
        const response = await fetch("/auth-config.json", {
          cache: "no-store",
          credentials: "same-origin",
          signal: AbortSignal.timeout(10000),
        });
        if (response.ok) config = validateConfig(await response.json());
      } catch {
        /* Unconfigured deployments fail closed; no test credentials. */
      }
    }
    if (!config) return null;
    const manager = new UserManager({
      authority: config.authority,
      client_id: config.client_id,
      redirect_uri: `${window.location.origin}/auth/callback/`,
      post_logout_redirect_uri: `${window.location.origin}/auth/logout/`,
      response_type: "code",
      scope: config.scope,
      resource: config.resource,
      disablePKCE: false,
      loadUserInfo: false,
      automaticSilentRenew: false,
      monitorSession: false,
      userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
      stateStore: new WebStorageStateStore({ store: sessionStorage }),
      staleStateAgeInSeconds: 300,
      requestTimeoutInSeconds: 15,
    });
    await manager.clearStaleState();
    return manager;
  })();
  return managerPromise;
}

export async function accessToken(): Promise<string | null> {
  const manager = await authManager();
  const user = await manager?.getUser();
  if (
    !user ||
    user.expired ||
    !user.expires_at ||
    user.expires_at <= Date.now() / 1000
  ) {
    if (user) await manager?.removeUser();
    return null;
  }
  return user.access_token;
}

export async function signIn(): Promise<void> {
  const manager = await authManager();
  if (!manager)
    throw new Error("Staff sign-in is not configured for this environment.");
  await manager.signinRedirect();
}

export async function signOut(): Promise<void> {
  const manager = await authManager();
  if (!manager) return;
  // Clear application credentials even if the provider's logout request fails.
  const user = await manager.getUser();
  await manager.removeUser();
  sessionStorage.removeItem("gorgona_active_salon");
  if (await manager.metadataService.getEndSessionEndpoint()) {
    await manager.signoutRedirect({ id_token_hint: user?.id_token });
  }
}

export function completeSignIn(): Promise<void> {
  callbackPromise ??= (async () => {
    const manager = await authManager();
    if (!manager)
      throw new Error("Staff sign-in is not configured for this environment.");
    const callbackUrl = window.location.href;
    history.replaceState(null, "", "/auth/callback/");
    await manager.signinRedirectCallback(callbackUrl);
    if (!(await accessToken()))
      throw new Error("No valid access token was returned.");
  })();
  return callbackPromise;
}

export async function completeSignOut(): Promise<void> {
  const manager = await authManager();
  const callbackUrl = window.location.href;
  history.replaceState(null, "", "/auth/logout/");
  if (manager && new URL(callbackUrl).searchParams.has("state"))
    await manager.signoutRedirectCallback(callbackUrl);
  await manager?.removeUser();
}
