/**
 * Grouping publishing accounts by network, and choosing them a network at a time.
 *
 * Publish groups its destinations into a card per network with a count and one
 * reach-everything action, because choosing eight pages one at a time is the
 * work that page exists to remove. The campaign account picker listed the same
 * accounts flat, with no counts and no way to take a whole network at once, so
 * the same twenty accounts were a different amount of work depending on which
 * screen you were standing on.
 *
 * The arithmetic lives here rather than in either component so the two surfaces
 * cannot drift the way they had - see ADR 0025 on pickers sharing their loop
 * rather than each growing a copy of it.
 */

/** The least an account has to say for this module to group and key it. */
export type GroupableAccount = {
  id: string;
  platform: string;
  provider: string;
  /** Absent means available: only an engine that has answered says otherwise. */
  available?: boolean;
};

/**
 * One account's identity across a session.
 *
 * Provider and id together, because two engines can expose accounts under ids
 * that only look alike - a Buffer page and a Zernio page can both be "12".
 */
export function accountKey(account: GroupableAccount): string {
  return `${account.provider}:${account.id}`;
}

export type AccountGroup<T extends GroupableAccount> = {
  /**
   * The network, as narrow a type as the caller handed over.
   *
   * Taken from the account rather than widened to `string`, so a caller whose
   * accounts carry a union of known platforms gets that union back and can
   * pass it to something that only accepts one - which is most of what a group
   * header is for.
   */
  platform: T["platform"];
  accounts: T[];
  /** How many of this network's accounts are chosen. */
  chosen: number;
  /** How many can be chosen at all - the rest are out of quota or refused. */
  selectable: number;
  /**
   * Whether the network's reach-everything action would clear rather than fill.
   *
   * True only when every *selectable* account is already chosen: an account
   * the engine has refused can never be picked, so counting it would leave the
   * button saying "All" forever on a network that is already as full as it
   * goes.
   */
  allChosen: boolean;
};

/**
 * Accounts grouped by network, in the order the networks first appear.
 *
 * First appearance rather than alphabetical: the caller has already decided
 * what order to hand these over in - recommendation strength, usually - and
 * re-sorting here would quietly overrule it.
 */
export function groupByPlatform<T extends GroupableAccount>(
  accounts: T[],
  chosenKeys: ReadonlySet<string>,
): AccountGroup<T>[] {
  const groups = new Map<T["platform"], T[]>();
  for (const account of accounts) {
    const existing = groups.get(account.platform);
    if (existing) existing.push(account);
    else groups.set(account.platform, [account]);
  }
  return [...groups.entries()].map(([platform, list]) => {
    const selectable = list.filter((account) => account.available !== false);
    const chosen = list.filter((account) => chosenKeys.has(accountKey(account))).length;
    return {
      platform,
      accounts: list,
      chosen,
      selectable: selectable.length,
      allChosen: selectable.length > 0
        && selectable.every((account) => chosenKeys.has(accountKey(account))),
    };
  });
}

/**
 * The selection after a network's reach-everything action.
 *
 * Only touches this network, and only its selectable accounts. Clearing one
 * network must not clear another, and filling one must not pick something the
 * engine has already refused - which would send a request that fails.
 */
export function toggleGroup<T extends GroupableAccount>(
  group: AccountGroup<T>,
  chosenKeys: ReadonlySet<string>,
): Set<string> {
  const next = new Set(chosenKeys);
  for (const account of group.accounts) {
    if (account.available === false) continue;
    if (group.allChosen) next.delete(accountKey(account));
    else next.add(accountKey(account));
  }
  return next;
}

/** The selection after one account is turned on or off. */
export function toggleAccount(
  account: GroupableAccount,
  chosenKeys: ReadonlySet<string>,
): Set<string> {
  const next = new Set(chosenKeys);
  const key = accountKey(account);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

/**
 * Which of the chosen keys still refer to an account that is on offer.
 *
 * The list is reloaded while the dialog is open - an account can be
 * disconnected, or run out of quota, between choosing it and pressing Add. A
 * selection carrying a key nothing answers to any more sends a request that
 * fails on something the operator can no longer see.
 */
export function stillOffered<T extends GroupableAccount>(
  accounts: T[],
  chosenKeys: ReadonlySet<string>,
): Set<string> {
  const offered = new Set(
    accounts.filter((account) => account.available !== false).map(accountKey),
  );
  return new Set([...chosenKeys].filter((key) => offered.has(key)));
}
