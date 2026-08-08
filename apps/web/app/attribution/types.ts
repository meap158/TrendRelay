/** The shape of `GET /attribution/products`, kept beside the page that reads it. */

export type ProductOffer = {
  id: string;
  network: string;
  merchant: string | null;
  affiliate_url: string;
  currency: string;
  price_cents: number | null;
  commission_bps: number | null;
  cookie_days: number | null;
  availability: string;
};

export type ProductLink = {
  id: string;
  code: string;
  platform: string;
  destination_url: string;
  status: string;
  expires_at: string | null;
};

export type EarningsBucket = {
  currency: string;
  approved: number;
  pending: number;
  reversals: number;
  net_commission_cents: number;
  order_value_cents: number;
};

export type ProductRow = {
  id: string;
  name: string;
  brand: string | null;
  category: string | null;
  marketplace: string;
  identifier: string | null;
  product_url: string | null;
  image_url: string | null;
  offers: ProductOffer[];
  links: ProductLink[];
  clicks: number;
  earnings: EarningsBucket[];
  /**
   * Names only. The economics live in the sibling `works` list, once each,
   * because two editions of one book share one ad budget.
   */
  work_ids: string[];
  product_form: string | null;
};

export type WorkCurrency = {
  currency: string;
  spend_cents: number;
  royalty_cents: number;
  attributed_royalty_cents: number;
  units: number;
  impressions: number;
  clicks: number;
  roas: number | null;
  acos: number | null;
  tacos: number | null;
};

export type WorkRow = {
  work_id: string;
  title: string;
  currencies: WorkCurrency[];
  /** Product ids, so a work can point back at the rows above it. */
  editions: string[];
};

export type ProductsPayload = {
  products: ProductRow[];
  works: WorkRow[];
  count: number;
  note: string;
};
