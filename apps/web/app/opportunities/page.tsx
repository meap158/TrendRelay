"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "../auth-provider";

type Workspace = { id: string; name: string; role: string };
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
type ImportResult = {
  created: number;
  skipped: number;
  errors: { row: number; detail: string }[];
};

const sampleCsv = [
  "product_name,brand,category,marketplace,network,merchant,affiliate_url,product_url,price,currency,commission_percent,commission_flat,cookie_days,availability,restrictions",
  "Portable Espresso Maker,Example Brand,Kitchen,Amazon,Creators,Amazon,https://example.com/affiliate,https://example.com/product,89.99,USD,10,,7,available,US only|No paid search",
].join("\n");

const factorFields = [
  ["growth_velocity", "Growth velocity", 50],
  ["acceleration", "Acceleration", 50],
  ["buyer_intent", "Buyer intent", 50],
  ["creative_reproducibility", "Creative reproducibility", 50],
  ["freshness", "Freshness", 50],
  ["competition", "Competition penalty", 25],
  ["policy_risk", "Policy-risk penalty", 10],
] as const;

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Opportunity request failed.");
  return body;
}

function commaValues(value: FormDataEntryValue | null): string[] {
  return String(value ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
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

function money(cents: number | null | undefined, currency: string): string {
  if (cents == null) return "Price unknown";
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
  }).format(cents / 100);
}

export default function OpportunitiesPage() {
  const { loading, user, apiFetch } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [offers, setOffers] = useState<Offer[]>([]);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [csvText, setCsvText] = useState(sampleCsv);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [selectedOffers, setSelectedOffers] = useState<string[]>([]);
  const [selectedOffer, setSelectedOffer] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [prefill, setPrefill] = useState({ trend: "", evidence: "", job: "" });

  const workspace = workspaces.find((item) => item.id === workspaceId);
  const canEdit = ["owner", "editor"].includes(workspace?.role ?? "");
  const canScore = ["owner", "editor", "analyst"].includes(workspace?.role ?? "");

  const refresh = useCallback(
    async (nextWorkspace: string) => {
      if (!nextWorkspace) return;
      const [offerBody, opportunityBody] = await Promise.all([
        apiFetch(`/api/workspaces/${nextWorkspace}/opportunities/offers`).then(
          (response) => json<{ offers: Offer[] }>(response),
        ),
        apiFetch(`/api/workspaces/${nextWorkspace}/opportunities`).then(
          (response) => json<{ opportunities: Opportunity[] }>(response),
        ),
      ]);
      setOffers(offerBody.offers);
      setOpportunities(opportunityBody.opportunities);
      setSelectedOffers((current) =>
        current.filter((id) => offerBody.offers.some((offer) => offer.id === id)),
      );
      setSelectedOffer((current) =>
        offerBody.offers.some((offer) => offer.id === current) ? current : "",
      );
    },
    [apiFetch],
  );

  useEffect(() => {
    queueMicrotask(() => {
      const params = new URLSearchParams(window.location.search);
      const trend = params.get("trend") ?? "";
      const source = params.get("source") ?? "";
      const title = params.get("title") ?? "";
      const url = params.get("url") ?? "";
      const job = params.get("job") ?? "";
      setPrefill({
        trend,
        evidence: source && title ? `${source} | ${title} | ${url}` : "",
        job,
      });
    });
  }, []);


  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    apiFetch("/api/workspaces")
      .then((response) => json<{ workspaces: Workspace[] }>(response))
      .then((body) => {
        if (cancelled) return;
        setWorkspaces(body.workspaces);
        setWorkspaceId(body.workspaces[0]?.id ?? "");
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load workspaces.");
      });
    return () => {
      cancelled = true;
    };
  }, [apiFetch, user]);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    Promise.all([
      apiFetch(`/api/workspaces/${workspaceId}/opportunities/offers`).then(
        (response) => json<{ offers: Offer[] }>(response),
      ),
      apiFetch(`/api/workspaces/${workspaceId}/opportunities`).then(
        (response) => json<{ opportunities: Opportunity[] }>(response),
      ),
    ])
      .then(([offerBody, opportunityBody]) => {
        if (cancelled) return;
        setOffers(offerBody.offers);
        setOpportunities(opportunityBody.opportunities);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load opportunities.");
      });
    return () => {
      cancelled = true;
    };
  }, [apiFetch, workspaceId]);

  const offerById = useMemo(
    () => new Map(offers.map((offer) => [offer.id, offer])),
    [offers],
  );

  async function importCsv() {
    setBusy("import");
    setError(null);
    setMessage(null);
    try {
      const body = await json<{ import: ImportResult }>(
        await apiFetch(`/api/workspaces/${workspaceId}/opportunities/offers/import`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ csv_text: csvText }),
        }),
      );
      setImportResult(body.import);
      setMessage(`Imported ${body.import.created} offer${body.import.created === 1 ? "" : "s"}.`);
      await refresh(workspaceId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Offer import failed.");
    } finally {
      setBusy(null);
    }
  }

  async function loadCsv(file: File | undefined) {
    if (!file) return;
    if (file.size > 2_000_000) {
      setError("CSV files must be 2 MB or smaller.");
      return;
    }
    setCsvText(await file.text());
    setImportResult(null);
  }

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
      setError("Add at least one evidence row.");
      return;
    }
    const inputs = Object.fromEntries(
      factorFields.map(([key]) => [key, Number(form.get(key))]),
    );
    const reasons = Object.fromEntries(
      factorFields
        .map(([key]) => [key, String(form.get(`${key}_reason`) ?? "").trim()])
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
      setMessage(`Opportunity scored ${body.opportunity.score}/100 with visible evidence.`);
      await refresh(workspaceId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Opportunity could not be scored.");
    } finally {
      setBusy(null);
    }
  }

  async function createCampaign(item: Opportunity) {
    if (!window.confirm(`Create a draft campaign from “${item.name}”?`)) return;
    setBusy(item.id);
    setError(null);
    setMessage(null);
    try {
      const body = await json<{ campaign: { id: string; name: string } }>(
        await apiFetch(
          `/api/workspaces/${workspaceId}/opportunities/${item.id}/campaign`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
          },
        ),
      );
      setMessage(`Draft campaign “${body.campaign.name}” is ready.`);
      await refresh(workspaceId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Campaign could not be created.");
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <main className="opportunity-page"><p>Loading opportunities…</p></main>;
  if (!user) return <main className="opportunity-page"><h1>Sign in required</h1></main>;

  return (
    <main className="opportunity-page">
      <header className="opportunity-header">
        <div>
          <p className="eyebrow">EVIDENCE → ECONOMICS → CAMPAIGN</p>
          <h1>Opportunity workbench</h1>
          <p className="lede">
            Import offers, score demand with visible factors, and turn the best case into a draft campaign.
          </p>
        </div>
        <label className="workspace-picker">
          Workspace
          <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
            {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.role}</option>)}
          </select>
        </label>
      </header>

      {error && <p className="registry-error" role="alert">{error}</p>}
      {message && <p className="registry-message" role="status">{message}</p>}

      <section className="opportunity-steps">
        <article className="offer-import-panel">
          <p className="eyebrow">1 · OFFER CATALOG</p>
          <h2>Import affiliate offers</h2>
          <p>Use the CSV fallback for Amazon Creators, impact.com, Awin, or any manual affiliate link.</p>
          <Link className="inline-guide-link" href="/tools#amazon-access-guide">Where to get Amazon API access</Link>
          <label className="file-button">
            Load CSV
            <input type="file" accept=".csv,text/csv" onChange={(event) => void loadCsv(event.target.files?.[0])} />
          </label>
          <textarea aria-label="Affiliate offer CSV" rows={8} value={csvText} onChange={(event) => setCsvText(event.target.value)} />
          <button type="button" disabled={!canEdit || busy === "import"} onClick={() => void importCsv()}>
            {busy === "import" ? "Importing…" : "Validate and import"}
          </button>
          {importResult && (
            <div className="import-summary">
              <strong>{importResult.created} created · {importResult.skipped} already present</strong>
              {importResult.errors.map((item) => <small key={`${item.row}-${item.detail}`}>Row {item.row}: {item.detail}</small>)}
            </div>
          )}
        </article>

        <form className="opportunity-form" onSubmit={createOpportunity}>
          <p className="eyebrow">2 · SCORE THE CASE</p>
          <h2>Build an explainable opportunity</h2>
          {prefill.job && <p className="registry-message">Completed research job {prefill.job} will be attached as source evidence.</p>}
          <div className="opportunity-form-grid">
            <label>Name<input name="name" required minLength={2} defaultValue={prefill.trend} placeholder="Portable espresso acceleration" /></label>
            <label>Trend entity<input name="trend_entity" required minLength={2} defaultValue={prefill.trend} placeholder="portable espresso maker" /></label>
            <label>Lifecycle<select name="lifecycle" defaultValue="unknown"><option value="unknown">Unknown</option><option value="emerging">Emerging</option><option value="accelerating">Accelerating</option><option value="peaking">Peaking</option><option value="saturated">Saturated</option><option value="declining">Declining</option></select></label>
            <label>Markets<input name="markets" placeholder="US, TH" /></label>
            <label>Languages<input name="languages" placeholder="en, th" /></label>
          </div>
          <label>Summary<textarea name="summary" rows={3} required minLength={2} placeholder="What the evidence says and why it may convert." /></label>
          <label>
            Evidence
            <textarea name="evidence" rows={4} required={!prefill.job} defaultValue={prefill.evidence} placeholder={"source | evidence title | https://source.example\nanother_source | supporting signal | https://source.example"} />
            <small>One row per item. Every score keeps these source references.</small>
          </label>

          <div className="score-input-grid">
            {factorFields.map(([key, label, initial]) => (
              <fieldset key={key}>
                <label>{label}<input name={key} type="number" min={0} max={100} defaultValue={initial} required /></label>
                <input name={`${key}_reason`} placeholder="Evidence-based reason (recommended)" maxLength={500} />
              </fieldset>
            ))}
          </div>

          <div className="offer-picker">
            <div><h3>Matching offers</h3><small>{offers.length} catalog offers</small></div>
            {offers.length === 0 && <p>No offers yet. Import the CSV first, or score trend evidence without an offer.</p>}
            {offers.map((offer) => (
              <label key={offer.id} className={selectedOffers.includes(offer.id) ? "selected" : ""}>
                <input type="checkbox" checked={selectedOffers.includes(offer.id)} onChange={() => toggleOffer(offer.id)} />
                <span><strong>{offer.product.name}</strong><small>{offer.network} · {money(offer.price_cents, offer.currency)} · {offer.commission_bps == null ? "commission unknown" : `${(offer.commission_bps / 100).toFixed(2)}% commission`}</small></span>
              </label>
            ))}
            {selectedOffers.length > 0 && (
              <label>
                Primary campaign offer
                <select value={selectedOffer} onChange={(event) => setSelectedOffer(event.target.value)}>
                  <option value="">No primary offer</option>
                  {selectedOffers.map((id) => <option key={id} value={id}>{offerById.get(id)?.product.name} · {offerById.get(id)?.network}</option>)}
                </select>
              </label>
            )}
          </div>
          <button disabled={!canScore || busy === "score"}>{busy === "score" ? "Scoring…" : "Save explainable score"}</button>
        </form>
      </section>

      <section className="opportunity-results">
        <div className="section-heading">
          <div><p className="eyebrow">3 · DECIDE</p><h2>Ranked opportunities</h2></div>
          <Link href="/research">Gather more evidence</Link>
        </div>
        {opportunities.length === 0 && <p className="empty-state">No opportunities scored yet.</p>}
        {opportunities.map((item) => (
          <article className="opportunity-card" key={item.id}>
            <div className="opportunity-score"><strong>{item.score}</strong><span>/100</span><small>{item.score_version}</small></div>
            <div className="opportunity-card-body">
              <div className="job-heading"><div><span>{item.lifecycle} · {item.status}</span><h2>{item.name}</h2></div><small>{item.offer_ids.length} matching offers</small></div>
              <p>{item.summary}</p>
              <div className="score-breakdown">
                {item.score_breakdown.map((factor) => (
                  <details key={factor.factor}>
                    <summary><span>{factor.label}</span><strong>{factor.contribution > 0 ? "+" : ""}{factor.contribution.toFixed(1)}</strong></summary>
                    <p>{factor.reason}</p>
                    <small>Input {factor.value}/100 · weight {(factor.weight * 100).toFixed(0)}% · {factor.evidence_ids.length} evidence reference{factor.evidence_ids.length === 1 ? "" : "s"}</small>
                  </details>
                ))}
              </div>
              <div className="opportunity-actions">
                <button type="button" disabled={!canEdit || busy === item.id} onClick={() => void createCampaign(item)}>{busy === item.id ? "Creating…" : "Create campaign"}</button>
                <Link href="/campaigns">Open campaign calendar</Link>
              </div>
            </div>
          </article>
        ))}
      </section>
    </main>
  );
}
