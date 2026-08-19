"use client";

import { ChangeEvent, FormEvent, useState } from "react";

import { ChoiceGroup } from "../ui/choice-group";
import { useT } from "../i18n-provider";

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;
type Mode = "export" | "links";

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
  affiliate_links: { offer_id: string; url: string; product: string }[];
  problems: string[];
};

const MAX_FILE_BYTES = 5 * 1024 * 1024;

export function ShopeeImport({
  workspaceId,
  apiFetch,
  succeed,
  fail,
  onImported,
  campaigns = [],
}: {
  workspaceId: string;
  apiFetch: Fetcher;
  succeed: (message: string) => void;
  fail: (message: string) => void;
  /** `clean` is true when nothing was skipped, so the caller can close the
      dialog on a tidy import and keep it open when there are problems to read. */
  onImported: (clean: boolean) => void;
  /** Campaigns these products could be imported for. */
  campaigns?: { id: string; name: string; tagged_products: number }[];
}) {
  const t = useT();
  const [mode, setMode] = useState<Mode>("export");
  const [xlsxBase64, setXlsxBase64] = useState("");
  const [csvText, setCsvText] = useState("");
  const [workbookName, setWorkbookName] = useState("");
  const [fileKey, setFileKey] = useState(0);
  const [links, setLinks] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState<"" | "open" | "preview" | "import">("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  /**
   * Campaigns these products will be allowed to promote.
   *
   * Asked here because importing a batch is nearly always importing it *for*
   * something, and tagging it afterwards is the same decision made twice - the
   * second one being the pass people forget, leaving a catalogue of products
   * no campaign can use.
   */
  const [importFor, setImportFor] = useState<Set<string>>(new Set());

  const source = mode === "export"
    ? { xlsx_base64: xlsxBase64, csv_text: csvText, links: "" }
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

  function chooseExportFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    setPreview(null);
    setOutcome(null);
    if (!file) {
      setXlsxBase64("");
      setCsvText("");
      setWorkbookName("");
      return;
    }
    const name = file.name.toLowerCase();
    if (!name.endsWith(".csv") && !name.endsWith(".xlsx")) {
      fail(t("attribution.shopee.chooseExport"));
      event.target.value = "";
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      fail(t("attribution.shopee.fileTooLarge"));
      event.target.value = "";
      return;
    }
    const isCsv = name.endsWith(".csv");
    const reader = new FileReader();
    reader.onerror = () => fail(t("attribution.shopee.fileUnreadable"));
    reader.onload = () => {
      const result = String(reader.result ?? "");
      const encoded = isCsv ? "" : result.split(",", 2)[1] ?? "";
      const text = isCsv ? result.replace(/^\uFEFF/, "") : "";
      setXlsxBase64(encoded);
      setCsvText(text);
      setWorkbookName(file.name);
      void previewSource({ xlsx_base64: encoded, csv_text: text, links: "" });
    };
    if (isCsv) reader.readAsText(file, "utf-8");
    else reader.readAsDataURL(file);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("import");
    setOutcome(null);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/attribution/shopee/import`,
        {
          method: "POST",
          body: JSON.stringify({
            ...source,
            confirm_external_action: true,
            campaign_ids: [...importFor],
            // The file this batch came from, so the catalogue can be filtered
            // back to it. A pasted export or links have no name to record.
            filename: workbookName || null,
          }),
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? t("attribution.shopee.importRefused"));
      setOutcome(payload as Outcome);
      const tagged = Object.values(
        (payload.tagged_to_campaigns ?? {}) as Record<string, { tagged: number }>,
      ).reduce((total, item) => total + (item?.tagged ?? 0), 0);
      succeed(tagged
        ? `${t("attribution.shopee.importedCount", { count: payload.created })} `
          + `${tagged} tagged to ${importFor.size} campaign${importFor.size === 1 ? "" : "s"}.`
        : t("attribution.shopee.importedCount", { count: payload.created }));
      setXlsxBase64("");
      setCsvText("");
      setWorkbookName("");
      setLinks("");
      setPreview(null);
      setFileKey((value) => value + 1);
      // A clean import has nothing left to read here, so let the caller close
      // the dialog; leave it open when there are problems worth acting on.
      onImported((payload.problems ?? []).length === 0);
    } catch (problem) {
      fail(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy("");
    }
  }

  const hasSource = mode === "export" ? Boolean(xlsxBase64 || csvText) : Boolean(links.trim());
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
            value: "export",
            label: t("attribution.shopee.exportRecommended"),
            description: t("attribution.shopee.exportDescription"),
          },
          {
            value: "links",
            label: t("attribution.shopee.linksOption"),
            description: t("attribution.shopee.linksDescription"),
          },
        ]}
      />

      {mode === "export" ? (
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
            {t("attribution.shopee.fileLabel")}
            <input
              key={fileKey}
              type="file"
              accept=".csv,text/csv,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={chooseExportFile}
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

      <p className="attribution-note">{t("attribution.shopee.directLinkNote")}</p>
      {/* What this batch is for. A product no campaign may promote is filed
          and unusable, so the question is asked where the batch arrives rather
          than left to a second pass on another screen. */}
      {campaigns.length > 0 && (
        <fieldset className="shopee-import-campaigns">
          <legend>Import these for</legend>
          <div>
            {campaigns.map((campaign) => (
              <label key={campaign.id}>
                <input
                  type="checkbox"
                  checked={importFor.has(campaign.id)}
                  onChange={() => setImportFor((current) => {
                    const next = new Set(current);
                    if (next.has(campaign.id)) next.delete(campaign.id);
                    else next.add(campaign.id);
                    return next;
                  })}
                />
                <span>{campaign.name}</span>
                <small>{campaign.tagged_products} tagged</small>
              </label>
            ))}
          </div>
          <small>{importFor.size
            ? "Imported products can be promoted by the campaigns ticked above."
            : "Tick none and the products are filed untagged - no campaign will be able to use them until you tag them."}</small>
        </fieldset>
      )}
      <form onSubmit={submit}>
        <button
          className="ui-button ui-button-primary ui-button-md"
          disabled={!hasSource || !preview || preview.readable === 0 || busy !== ""}
        >
          {busy === "import"
            ? t("attribution.importing")
            : t("attribution.shopee.importAction", { count: importCount || preview?.readable || 0 })}
        </button>
      </form>

      {outcome && (
        <div className="attribution-import-outcome" role="status">
          <p>{t("attribution.shopee.importOutcome", { created: outcome.created, links: outcome.affiliate_links.length })}</p>
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
