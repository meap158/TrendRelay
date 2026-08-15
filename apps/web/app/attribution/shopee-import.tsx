"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useState } from "react";

import { ChoiceGroup } from "../ui/choice-group";
import { useT } from "../i18n-provider";

type Campaign = { id: string; name: string };
type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;
type Mode = "excel" | "links";

type Preview = {
  readable: number;
  new_offers: number;
  already_present: number;
  duplicates_in_file: number;
  problems: string[];
};

type Outcome = {
  created: number;
  already_present: number;
  links: { id: string; code: string; product: string }[];
  problems: string[];
  enriching: number;
};

const PLATFORMS = ["tiktok", "instagram", "youtube", "douyin", "other"] as const;
const MAX_FILE_BYTES = 5 * 1024 * 1024;

export function ShopeeImport({
  workspaceId,
  campaigns,
  apiFetch,
  succeed,
  fail,
  onImported,
}: {
  workspaceId: string;
  campaigns: Campaign[];
  apiFetch: Fetcher;
  succeed: (message: string) => void;
  fail: (message: string) => void;
  onImported: () => void;
}) {
  const t = useT();
  const [mode, setMode] = useState<Mode>("excel");
  const [campaignId, setCampaignId] = useState("");
  const [platform, setPlatform] = useState<string>("tiktok");
  const [disclosure, setDisclosure] = useState("Affiliate link");
  const [xlsxBase64, setXlsxBase64] = useState("");
  const [workbookName, setWorkbookName] = useState("");
  const [fileKey, setFileKey] = useState(0);
  const [links, setLinks] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState<"" | "open" | "preview" | "import">("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const source = mode === "excel"
    ? { xlsx_base64: xlsxBase64, csv_text: "", links: "" }
    : { xlsx_base64: "", csv_text: "", links };

  async function previewSource(nextSource = source) {
    setBusy("preview");
    setPreview(null);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/attribution/shopee/import/preview`,
        { method: "POST", body: JSON.stringify(nextSource) },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? t("attribution.shopee.exportUnreadable"));
      setPreview(payload as Preview);
    } catch (problem) {
      fail(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy("");
    }
  }

  async function openShopee() {
    setBusy("open");
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/attribution/shopee/offers/open`,
        { method: "POST", body: JSON.stringify({ confirm_external_action: true }) },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? t("attribution.shopee.openFailed"));
      succeed(t("attribution.shopee.opened"));
    } catch (problem) {
      fail(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy("");
    }
  }

  function chooseWorkbook(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    setPreview(null);
    setOutcome(null);
    if (!file) {
      setXlsxBase64("");
      setWorkbookName("");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      fail(t("attribution.shopee.chooseXlsx"));
      event.target.value = "";
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      fail(t("attribution.shopee.fileTooLarge"));
      event.target.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => fail(t("attribution.shopee.fileUnreadable"));
    reader.onload = () => {
      const encoded = String(reader.result ?? "").split(",", 2)[1] ?? "";
      setXlsxBase64(encoded);
      setWorkbookName(file.name);
      void previewSource({ xlsx_base64: encoded, csv_text: "", links: "" });
    };
    reader.readAsDataURL(file);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!campaignId) {
      fail(t("attribution.shopee.chooseCampaignError"));
      return;
    }
    setBusy("import");
    setOutcome(null);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/attribution/shopee/import`,
        {
          method: "POST",
          body: JSON.stringify({
            campaign_id: campaignId,
            platform,
            disclosure,
            ...source,
            confirm_external_action: true,
          }),
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? t("attribution.shopee.importRefused"));
      setOutcome(payload as Outcome);
      succeed(t("attribution.shopee.importedCount", { count: payload.created }));
      setXlsxBase64("");
      setWorkbookName("");
      setLinks("");
      setPreview(null);
      setFileKey((value) => value + 1);
      onImported();
    } catch (problem) {
      fail(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy("");
    }
  }

  const hasSource = mode === "excel" ? Boolean(xlsxBase64) : Boolean(links.trim());
  const importCount = preview?.new_offers ?? 0;

  return (
    <article className="attribution-panel attribution-panel-bare shopee-import-flow">
      <ChoiceGroup
        name="shopee-import-method"
        legend={t("attribution.shopee.method")}
        value={mode}
        onChange={(next) => { setMode(next); setPreview(null); setOutcome(null); }}
        options={[
          {
            value: "excel",
            label: t("attribution.shopee.excelRecommended"),
            description: t("attribution.shopee.excelDescription"),
          },
          {
            value: "links",
            label: t("attribution.shopee.linksOption"),
            description: t("attribution.shopee.linksDescription"),
          },
        ]}
      />

      {mode === "excel" ? (
        <section className="shopee-export-path" aria-labelledby="shopee-export-heading">
          <div>
            <h3 id="shopee-export-heading">{t("attribution.shopee.exportHeading")}</h3>
            <ol>
              <li>{t("attribution.shopee.stepOpen")}</li>
              <li>{t("attribution.shopee.stepExport")}</li>
              <li>{t("attribution.shopee.stepChoose")}</li>
            </ol>
          </div>
          <button
            type="button"
            className="ui-button ui-button-secondary ui-button-md"
            onClick={() => void openShopee()}
            disabled={busy !== ""}
          >{busy === "open" ? t("attribution.shopee.opening") : t("attribution.shopee.openOffer")}</button>
          <label>
            {t("attribution.shopee.excelLabel")}
            <input
              key={fileKey}
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={chooseWorkbook}
              disabled={busy !== ""}
            />
            <small>{workbookName || t("attribution.shopee.fileLimits")}</small>
          </label>
        </section>
      ) : (
        <label>
          {t("attribution.shopee.linksLabel")}
          <textarea
            rows={5}
            value={links}
            onChange={(event) => { setLinks(event.target.value); setPreview(null); }}
            placeholder="https://shopee.vn/product/…"
            spellCheck={false}
          />
          <small>{t("attribution.shopee.linksHelp")}</small>
          <button
            type="button"
            className="ui-button ui-button-secondary ui-button-sm"
            onClick={() => void previewSource()}
            disabled={!links.trim() || busy !== ""}
          >{busy === "preview" ? t("attribution.shopee.reviewing") : t("attribution.shopee.reviewLinks")}</button>
        </label>
      )}

      {preview && (
        <div className="shopee-import-preview" role="status">
          <strong>{t("attribution.shopee.readableCount", { count: preview.readable })}</strong>
          <span>{t("attribution.shopee.newCount", { count: preview.new_offers })}</span>
          <span>{t("attribution.shopee.existingCount", { count: preview.already_present })}</span>
          {preview.duplicates_in_file > 0 && <span>{t("attribution.shopee.duplicateCount", { count: preview.duplicates_in_file })}</span>}
          {preview.problems.length > 0 && (
            <ul>{preview.problems.slice(0, 5).map((problem) => <li key={problem}>{problem}</li>)}</ul>
          )}
        </div>
      )}

      {campaigns.length === 0 ? (
        <p className="attribution-note">
          {t("attribution.shopee.campaignRequired")}{" "}
          <Link href="/campaigns">{t("attribution.shopee.createCampaign")}</Link>{t("attribution.shopee.returnHere")}
        </p>
      ) : (
        <form onSubmit={submit}>
          <div className="attribution-form-row">
            <label>
              {t("attribution.campaign")}
              <select value={campaignId} onChange={(event) => setCampaignId(event.target.value)}>
                <option value="">{t("attribution.shopee.chooseCampaign")}</option>
                {campaigns.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </label>
            <label>
              {t("attribution.shopee.platform")}
              <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
                {PLATFORMS.map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </label>
          </div>
          <label>
            {t("attribution.shopee.disclosure")}
            <input value={disclosure} onChange={(event) => setDisclosure(event.target.value)} />
            <small>{t("attribution.shopee.disclosureHelp")}</small>
          </label>
          <button
            className="ui-button ui-button-primary ui-button-md"
            disabled={!campaignId || !hasSource || !preview || preview.readable === 0 || busy !== ""}
          >
            {busy === "import"
              ? t("attribution.importing")
              : t("attribution.shopee.importAction", { count: importCount || preview?.readable || 0 })}
          </button>
        </form>
      )}

      {outcome && (
        <div className="attribution-import-outcome" role="status">
          <p>{t("attribution.shopee.importOutcome", { created: outcome.created, links: outcome.links.length })}</p>
          {outcome.already_present > 0 && <p>{t("attribution.shopee.unchangedCount", { count: outcome.already_present })}</p>}
          {outcome.problems.length > 0 && (
            <ul className="attribution-import-problems">
              {outcome.problems.map((problem) => <li key={problem}>{problem}</li>)}
            </ul>
          )}
        </div>
      )}
    </article>
  );
}
