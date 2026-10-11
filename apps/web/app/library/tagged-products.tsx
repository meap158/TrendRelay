"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useT } from "../i18n-provider";
import { Button } from "../ui/button";

export type TaggedProduct = {
  product_id: string;
  name: string;
  image_url?: string | null;
  draft_id: string | null;
};

/** How many rows show before the rest fold behind "Show more". */
const VISIBLE = 3;

/**
 * The Attribution products an asset was made for, as rows a person can scan.
 *
 * Marketplace titles run to a hundred characters of keywords, so a name alone
 * per line read as a paragraph of links. Each row leads with the product's
 * picture - the thing a person recognises first - and clamps the title to two
 * lines, with the whole title kept in the tooltip. Past a handful the rest
 * fold away, so a group shot tagged with many products does not push the
 * File actions off the panel.
 */
export function TaggedProducts({ products, onRemove }: {
  products: TaggedProduct[];
  onRemove?: (productId: string) => Promise<void>;
}) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function remove(productId: string) {
    setRemoving(productId); setError(null);
    try { await onRemove?.(productId); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not remove product."); }
    finally { setRemoving(null); }
  }
  const hidden = products.length - VISIBLE;
  const shown = expanded || hidden <= 1 ? products : products.slice(0, VISIBLE);
  return (
    <div className="library-tagged-products">
      <div className="library-tagged-products-head">
        <span>{t("library.attributionProducts")}</span>
        <span className="library-tagged-products-count">{products.length}</span>
      </div>
      <ul>
        {shown.map((product) => (
          <li key={product.product_id}>
            <Link
              className="library-tagged-product"
              href={`/attribution?products=${encodeURIComponent(product.product_id)}`}
              title={product.name}
            >
              {product.image_url
                // eslint-disable-next-line @next/next/no-img-element
                ? <img className="product-thumb" src={product.image_url} alt="" loading="lazy" />
                : <span className="product-thumb product-thumb-initial" aria-hidden="true">
                    {(product.name.trim()[0] ?? "?").toUpperCase()}
                  </span>}
              <span className="library-tagged-product-name">{product.name}</span>
              <ChevronRight size={14} aria-hidden="true" />
            </Link>
            {onRemove && <Button variant="quiet" size="sm" busy={removing === product.product_id}
              disabled={removing !== null} aria-label={`Remove ${product.name} from this media`}
              title="Remove this product attachment"
              onClick={() => void remove(product.product_id)}>Remove</Button>}
          </li>
        ))}
      </ul>
      {error && <p role="alert">{error}</p>}
      {hidden > 1 && (
        <button
          type="button"
          className="library-tagged-products-more"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded
            ? t("library.showFewerProducts")
            : t("library.showMoreProducts", { count: hidden })}
        </button>
      )}
    </div>
  );
}
