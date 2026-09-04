"use client";

import { useEffect, useState } from "react";
import { useLocale } from "../../i18n-provider";
import { Button } from "../../ui/button";
import { Dialog } from "../../ui/dialog";
import { Badge } from "../../ui/primitives";
import styles from "./manage.module.css";

type Warning = {
  id: string;
  state: "failed" | "uncertain";
  title: string | null;
  caption: string;
  first_comment: string | null;
  destination_label: string | null;
  platform: string;
  provider: string;
  error: string | null;
  updated_at: string;
};
type WarningPage = { total: number; warnings: Warning[] };

/** A temporary, read-only drilldown: the control room stays in place underneath. */
export function CampaignWarningsDialog({
  campaign, workspaceId, startsAt, endsAt, timezone, apiFetch, onClose,
}: {
  campaign: { id: string; name: string };
  workspaceId: string;
  startsAt: string;
  endsAt: string;
  timezone: string;
  apiFetch: (input: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
}) {
  const { t, locale } = useLocale();
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<WarningPage | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(true);
  const [retry, setRetry] = useState(0);
  const limit = 50;
  const date = (value: string) => new Date(value).toLocaleString(locale, {
    timeZone: timezone, month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ starts_at: startsAt, ends_at: endsAt,
      limit: String(limit), offset: String(offset) });
    queueMicrotask(() => { setBusy(true); setError(false); });
    void apiFetch(`/api/workspaces/${workspaceId}/campaigns/${campaign.id}/management/warnings?${params}`,
      { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Warning list unavailable");
        return await response.json() as WarningPage;
      })
      .then((body) => { if (!controller.signal.aborted) setPage(body); })
      .catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [apiFetch, campaign.id, endsAt, offset, retry, startsAt, workspaceId]);

  return <Dialog open title={t("campaignWarnings.title", { campaign: campaign.name })}
    description={t("campaignWarnings.range", { start: date(startsAt), end: date(endsAt) })}
    onClose={onClose}>
    <div className={styles.warningToolbar} aria-live="polite">
      <span>{busy ? t("campaignWarnings.loading") : error ? t("campaignWarnings.loadError")
        : t("campaignWarnings.count", { count: page?.total ?? 0 })}</span>
      {error ? <Button size="sm" variant="secondary" onClick={() => setRetry((value) => value + 1)}>
        {t("campaignWarnings.retry")}</Button> : (page?.total ?? 0) > limit && <span>
        <Button size="sm" variant="quiet" disabled={busy || offset === 0}
          onClick={() => setOffset((value) => Math.max(0, value - limit))}>{t("campaignWarnings.previous")}</Button>
        <Button size="sm" variant="quiet" disabled={busy || offset + limit >= (page?.total ?? 0)}
          onClick={() => setOffset((value) => value + limit)}>{t("campaignWarnings.next")}</Button>
      </span>}
    </div>
    <ol className={styles.warningList} aria-busy={busy}>
      {!busy && !error && page?.warnings.map((item) => <li key={item.id}>
        <header><strong>{item.title || item.caption.slice(0, 100) || t("campaignWarnings.untitled")}</strong>
          <Badge tone="bad">{t(`campaignWarnings.${item.state}`)}</Badge></header>
        <small>{item.destination_label || item.platform} · {item.platform} · {item.provider} · {date(item.updated_at)}</small>
        <p className={styles.warningError}>{item.error || t("campaignWarnings.noReason")}</p>
        {item.state === "uncertain" && <p>{t("campaignWarnings.uncertainHint")}</p>}
        <details><summary>{t("campaignWarnings.details")}</summary>
          <p className={styles.warningCopy}>{item.caption}</p>
          {item.first_comment && <p className={styles.warningCopy}>{item.first_comment}</p>}
          <small>{t("campaignWarnings.reference", { id: item.id })}</small>
        </details>
      </li>)}
    </ol>
    {!busy && !error && page?.total === 0 && <p role="status">{t("campaignWarnings.empty")}</p>}
  </Dialog>;
}
