/**
 * Naming the login behind an engine, the same way everywhere it is shown.
 *
 * An engine is capabilities and a connection is one login to it, and there may
 * be several. "Buffer" and "Buffer · second" say which row you are looking at
 * but not which account it posts from, and a label somebody typed months ago is
 * only as good as their memory of typing it. The engines themselves know, so
 * the probe that checks the key now brings the answer back with it.
 *
 * What they will say differs. Only Buffer names an email; Zernio gives the
 * owner's name, Bundle.social an organisation, WoopSocial a project. This
 * prefers the email because it is the thing somebody recognises as an account,
 * and falls back to whatever name was offered.
 *
 * Returns null rather than a placeholder when the engine names nobody - which
 * is also the state of a refused key, where there is no account to name. The
 * caller decides what to show instead, and "Unknown account" under two
 * identical cards is not it.
 */
export type EngineAccount = { email?: string; name?: string; scope?: string };

export function accountIdentity(source: { account?: EngineAccount } | null | undefined): string | null {
  const account = source?.account;
  if (!account) return null;
  const email = account.email?.trim();
  if (email) return email;
  const name = account.name?.trim();
  if (!name) return null;
  // Qualified where the name is not a person's account: an organisation or a
  // project read as an account name otherwise, which is a different claim.
  if (account.scope === "organisation") return `${name} (organisation)`;
  if (account.scope === "project") return `${name} (project)`;
  return name;
}
