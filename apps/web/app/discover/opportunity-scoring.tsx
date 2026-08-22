"use client";

/**
 * Turning research into a scored case, where the research is.
 *
 * This was its own page reached by a link from Discover carrying the trend,
 * the evidence and the research job id in the query string - a hand-off
 * between two pages that existed only because they were two pages. The
 * evidence is here; the decision belongs here too.
 *
 * The score is deliberately not a black box. Every factor keeps the number you
 * gave it, the weight applied to it and the reason you typed, and the ranked
 * list below shows all three. A score you cannot argue with is a score nobody
 * trusts a week later.
 */

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "../ui/button";
import { Select } from "../ui/select";
import { useT } from "../i18n-provider";

type Offer = {
  id: string;
  product: {
    name: string;
    brand?: string | null;
    category?: string | null;
    marketplace: string;
  };
  network: string;
  merchant?: string | null;
  affiliate_url: string;
  price_cents?: number | null;
  currency: string;
  commission_bps?: number | null;
  availability: string;
};

type Breakdown = {
  factor: string;
  label: string;
  value: number;
  weight: number;
  contribution: number;
  reason: string;
  evidence_ids: string[];
};

type Opportunity = {
  id: string;
  name: string;
  trend_entity: string;
  summary: string;
  lifecycle: string;
  markets: string[];
  languages: string[];
  score: number;
  score_version: string;
  score_breakdown: Breakdown[];
  offer_ids: string[];
  selected_offer_id?: string | null;
  status: string;
};

const FACTORS = [
  ["growth_velocity", "opportunities.growthVelocity", 50],
  ["acceleration", "opportunities.acceleration", 50],
  ["buyerIntent", "opportunities.buyerIntent", 50],
  ["creative_reproducibility", "opportunities.creativeReproducibility", 50],
  ["freshness", "opportunities.freshness", 50],
  ["competition", "opportunities.competitionPenalty", 25],
  ["policy_risk", "opportunities.policyRiskPenalty", 10],
] as const;

/** The keys the API expects, which are not all the keys the labels use. */
const FIELD_NAMES: Record<string, string> = { buyerIntent: "buyer_intent" };

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Opportunity request failed.");
  return body;
}

function commaValues(value: FormDataEntryValue | null): string[] {
  return String(value ?? "").split(",").map((item) => item.trim()).filter(Boolean);
}

function evidenceRows(value: FormDataEntryValue | null) {
  return String(value ?? "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      const [source = "manual", title = line, sourceUrl = ""] = line
        .split("|")
        .map((item) => item.trim());
      return {
        id: `manual-${index + 1}`,
        source,
        title,
        source_url: sourceUrl || null,
        metrics: {},
      };
    });
}

function money(cents: number | null | undefined, currency: string, unknown: string): string {
  if (cents == null) return unknown;
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100);
}

export function OpportunityScoring({
  workspaceId,
  role,
  apiFetch,
  /** Prefilled from the research run being scored, so nothing is retyped. */
  prefill,
}: {
  workspaceId: string;
  role: string;
  apiFetch: (input: string, init?: RequestInit) => Promise<Response>;
  prefill: { trend: string; evidence: string; job: string };
}) {
  const t = useT();
  const [offers, setOffers] = useState<Offer[]>([]);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [selectedOffers, setSelectedOffers] = useState<string[]>([]);
  const [selectedOffer, setSelectedOffer] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // Closed until asked for. Scoring is a deliberate step, and an open form
  // above the results would push the research itself off the screen. Arriving
  // with a trend to score opens it; the caller remounts this on a new prefill
  // rather than syncing state in an effect, which would fight the toggle.
  const [open, setOpen] = useState(Boolean(prefill.trend));

  const canEdit = ["owner", "editor"].includes(role);
  const canScore = ["owner", "editor", "analyst"].includes(role);

  const refresh = useCallback(async () => {
    if (!workspaceId) return;
    const [offerBody, opportunityBody] = await Promise.all([
      apiFetch(`/api/workspaces/${workspaceId}/opportunities/offers`)
        .then((response) => json<{ offers: Offer[] }>(response)),
      apiFetch(`/api/workspaces/${workspaceId}/opportunities`)
        .then((response) => json<{ opportunities: Opportunity[] }>(response)),
    ]);
    setOffers(offerBody.offers);
    setOpportunities(opportunityBody.opportunities);
    setSelectedOffers((current) =>
      current.filter((id) => offerBody.offers.some((offer) => offer.id === id)));
    setSelectedOffer((current) =>
      offerBody.offers.some((offer) => offer.id === current) ? current : "");
  }, [apiFetch, workspaceId]);

  useEffect(() => {
    if (!workspaceId) return;
    // Deferred out of the effect body: the load settles state, and doing that
    // synchronously here is the cascading-render pattern React warns about.
    queueMicrotask(() => {
      refresh().catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Could not load opportunities."));
    });
  }, [refresh, workspaceId]);

  const offerById = useMemo(
    () => new Map(offers.map((offer) => [offer.id, offer])),
    [offers],
  );

  function toggleOffer(id: string) {
    const next = selectedOffers.includes(id)
      ? selectedOffers.filter((item) => item !== id)
      : [...selectedOffers, id];
    setSelectedOffers(next);
    if (!next.includes(selectedOffer)) setSelectedOffer(next[0] ?? "");
  }

  async function createOpportunity(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const evidence = evidenceRows(form.get("evidence"));
    if (evidence.length === 0 && !prefill.job) {
      setError(t("opportunities.needEvidence"));
      return;
    }
    const inputs = Object.fromEntries(
      FACTORS.map(([key]) => [FIELD_NAMES[key] ?? key, Number(form.get(key))]),
    );
    const reasons = Object.fromEntries(
      FACTORS
        .map(([key]) => [
          FIELD_NAMES[key] ?? key,
          String(form.get(`${key}_reason`) ?? "").trim(),
        ])
        .filter(([, value]) => value),
    );
    setBusy("score");
    setError(null);
    setMessage(null);
    try {
      const body = await json<{ opportunity: Opportunity }>(
        await apiFetch(`/api/workspaces/${workspaceId}/opportunities`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: form.get("name"),
            trend_entity: form.get("trend_entity"),
            summary: form.get("summary"),
            lifecycle: form.get("lifecycle"),
            markets: commaValues(form.get("markets")),
            languages: commaValues(form.get("languages")),
            evidence,
            source_research_job_id: prefill.job || null,
            inputs: { ...inputs, reasons },
            offer_ids: selectedOffers,
            selected_offer_id: selectedOffer || null,
          }),
        }),
      );
      setMessage(t("opportunities.scored", { score: body.opportunity.score }));
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Opportunity could not be scored.");
    } finally {
      setBusy(null);
    }
  }

  async function createCampaign(item: Opportunity) {
    if (!window.confirm(t("opportunities.confirmCampaign", { name: item.name }))) return;
    setBusy(item.id);
    setError(null);
    setMessage(null);
    try {
      const body = await json<{ campaign: { id: string; name: string } }>(
        await apiFetch(`/api/workspaces/${workspaceId}/opportunities/${item.id}/campaign`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        }),
      );
      setMessage(t("opportunities.campaignReady", { name: body.campaign.name }));
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Campaign could not be created.");
    } finally {
      setBusy(null);
    }
  }

  if (!workspaceId) return null;

  return (
    <section className="opportunity-scoring">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t("opportunities.stepScore")}</p>
          <h2>{t("opportunities.buildCase")}</h2>
        </div>
        <Button variant={open ? "quiet" : "primary"} size="sm" onClick={() => setOpen((current) => !current)}>
          {open ? t("common.close") : t("opportunities.scoreATrend")}
        </Button>
      </div>

      {error && <p className="registry-error" role="alert">{error}</p>}
      {message && <p className="registry-message" role="status">{message}</p>}

      {open && (
        <form className="opportunity-form" onSubmit={createOpportunity}>
          {prefill.job && (
            <p className="registry-message">
              {t("opportunities.jobAttached", { job: prefill.job })}
            </p>
          )}
          <div className="opportunity-form-grid">
            <label>{t("campaigns.name")}
              <input name="name" required minLength={2} defaultValue={prefill.trend} />
            </label>
            <label>{t("opportunities.trendEntity")}
              <input name="trend_entity" required minLength={2} defaultValue={prefill.trend} />
            </label>
            <label>{t("opportunities.lifecycle")}
              <Select name="lifecycle" defaultValue="unknown">
                <option value="unknown">{t("lifecycle.unknown")}</option>
                <option value="emerging">{t("lifecycle.emerging")}</option>
                <option value="accelerating">{t("lifecycle.accelerating")}</option>
                <option value="peaking">{t("lifecycle.peaking")}</option>
                <option value="saturated">{t("lifecycle.saturated")}</option>
                <option value="declining">{t("lifecycle.declining")}</option>
              </Select>
            </label>
            <label>{t("campaigns.markets")}<input name="markets" placeholder="US, TH" /></label>
            <label>{t("campaigns.languages")}<input name="languages" placeholder="en, th" /></label>
          </div>
          <label>{t("opportunities.summary")}
            <textarea
              name="summary"
              rows={3}
              required
              minLength={2}
              placeholder={t("opportunities.whatEvidenceSays")}
            />
          </label>
          <label>{t("opportunities.evidence")}
            <textarea
              name="evidence"
              rows={4}
              required={!prefill.job}
              defaultValue={prefill.evidence}
              placeholder={"source | evidence title | https://source.example"}
            />
            <small>{t("opportunities.oneRowPerItem")}</small>
          </label>

          <div className="score-input-grid">
            {FACTORS.map(([key, label, initial]) => (
              <fieldset key={key}>
                <label>{t(label)}
                  <input name={key} type="number" min={0} max={100} defaultValue={initial} required />
                </label>
                <input name={`${key}_reason`} placeholder={t("opportunities.evidenceReason")} maxLength={500} />
              </fieldset>
            ))}
          </div>

          <div className="offer-picker">
            <div>
              <h3>{t("opportunities.matchingOffers")}</h3>
              <small>{t("opportunities.catalogOffers", { count: offers.length })}</small>
            </div>
            {offers.length === 0 && <p>{t("opportunities.noOffers")}</p>}
            {offers.map((offer) => (
              <label key={offer.id} className={selectedOffers.includes(offer.id) ? "selected" : ""}>
                <input
                  type="checkbox"
                  checked={selectedOffers.includes(offer.id)}
                  onChange={() => toggleOffer(offer.id)}
                />
                <span>
                  <strong>{offer.product.name}</strong>
                  <small>
                    {offer.network} · {money(offer.price_cents, offer.currency, t("opportunities.priceUnknown"))} ·{" "}
                    {offer.commission_bps == null
                      ? t("opportunities.commissionUnknown")
                      : t("opportunities.commissionRate", {
                          rate: (offer.commission_bps / 100).toFixed(2),
                        })}
                  </small>
                </span>
              </label>
            ))}
            {selectedOffers.length > 0 && (
              <label>{t("opportunities.primaryOffer")}
                <Select value={selectedOffer} onChange={(event) => setSelectedOffer(event.target.value)}>
                  <option value="">{t("opportunities.noPrimaryOffer")}</option>
                  {selectedOffers.map((id) => (
                    <option key={id} value={id}>
                      {offerById.get(id)?.product.name} · {offerById.get(id)?.network}
                    </option>
                  ))}
                </Select>
              </label>
            )}
          </div>
          <Button type="submit" variant="primary" busy={busy === "score"} disabled={!canScore}
            title={canScore ? undefined : "Only owners, editors and analysts can score an opportunity"}>
            {busy === "score" ? t("opportunities.scoring") : t("opportunities.saveScore")}
          </Button>
        </form>
      )}

      {opportunities.length > 0 && (
        <div className="opportunity-results">
          <div className="section-heading">
            <div>
              <p className="eyebrow">{t("opportunities.stepDecide")}</p>
              <h3>{t("opportunities.ranked")}</h3>
            </div>
          </div>
          {opportunities.map((item) => (
            <article className="opportunity-card" key={item.id}>
              <div className="opportunity-score">
                <strong>{item.score}</strong><span>/100</span><small>{item.score_version}</small>
              </div>
              <div className="opportunity-card-body">
                <div className="job-heading">
                  <div>
                    <span>{item.lifecycle} · {item.status}</span>
                    <h4>{item.name}</h4>
                  </div>
                  <small>{t("opportunities.matchingCount", { count: item.offer_ids.length })}</small>
                </div>
                <p>{item.summary}</p>
                {/* Collapsed, but present: the argument for the number has to be
                    reachable from the number itself, not from a report. */}
                <div className="score-breakdown">
                  {item.score_breakdown.map((factor) => (
                    <details key={factor.factor}>
                      <summary>
                        <span>{factor.label}</span>
                        <strong>{factor.contribution > 0 ? "+" : ""}{factor.contribution.toFixed(1)}</strong>
                      </summary>
                      <p>{factor.reason}</p>
                      <small>{t("opportunities.factorDetail", {
                        value: factor.value,
                        weight: (factor.weight * 100).toFixed(0),
                        evidence: factor.evidence_ids.length,
                      })}</small>
                    </details>
                  ))}
                </div>
                <div className="opportunity-actions">
                  <button
                    type="button"
                    disabled={!canEdit || busy === item.id}
                    onClick={() => void createCampaign(item)}
                  >{busy === item.id ? t("opportunities.creating") : t("opportunities.createCampaign")}</button>
                  <Link href="/campaigns">{t("opportunities.openCalendar")}</Link>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
