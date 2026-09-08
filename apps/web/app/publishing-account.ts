/**
 * Naming the login behind an engine, the same way everywhere it is shown.
 *
 * An engine is capabilities and a connection is one login to it, and there may
 * be several. "Buffer" and "Buffer · second" say which row you are looking at
 * but not which account it posts from, and a label somebody typed months ago is
 * only as good as their memory of typing it. The engines themselves know, so
 * the probe that checks the key now brings the answer back with it.
 *
 * What they will say differs. Buffer names an email; Zernio may return its
 * owner's email and name, Bundle.social an organisation, and WoopSocial a
 * project. This prefers the email because it is the thing somebody recognises
 * as an account, and falls back to whatever name was offered.
 *
 * Returns null rather than a placeholder when the engine names nobody - which
 * is also the state of a refused key, where there is no account to name. The
 * caller decides what to show instead, and "Unknown account" under two
 * identical cards is not it.
 */
export type EngineAccount = { email?: string; name?: string; scope?: string };

export function accountIdentity(
  ...sources: Array<{ account?: EngineAccount } | null | undefined>
): string | null {
  for (const source of sources) {
    const account = source?.account;
    if (!account) continue;
    const email = account.email?.trim();
    if (email) return email;
    const name = account.name?.trim();
    if (!name) continue;
    // Qualified where the name is not a person's account: an organisation or a
    // project read as an account name otherwise, which is a different claim.
    if (account.scope === "organisation") return `${name} (organisation)`;
    if (account.scope === "project") return `${name} (project)`;
    return name;
  }
  return null;
}
