/** The shape of `GET /attribution/products`, kept beside the page that reads it. */

export type ProductOffer = {
  id: string;
  network: string;
  merchant: string | null;
  affiliate_url: string;
  currency: string;
  price_cents: number | null;
  commission_bps: number | null;
  commission_flat_cents: number | null;
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
  /** The affiliate network's tracking parameters for this link, by slot -
      `{"sub_id1": "0968cda6328f", "sub_id4": "MyFirstCamp"}`. Optional so a
      caller reading an older payload stays valid. */
  sub_ids?: Record<string, string>;
};

export type EarningsBucket = {
  currency: string;
  approved: number;
  pending: number;
  reversals: number;
  net_commission_cents: number;
  order_value_cents: number;
};

/** What the product's own listing page said, row-sized. The full record -
    description, every image, variations, vouchers - is on the product's
    listing endpoint. Null until the page has been read once. */
export type ListingSummary = {
  discount_percent: number | null;
  shop_location: string | null;
  categories: string[];
  image_count: number;
  variation_count: number;
  voucher_count: number;
  has_video: boolean;
  listed_at: string | null;
  /** Figures Shopee only shows signed-in readers; named, never guessed. */
  withheld_signed_out: string[];
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
  /** Optional so older fixtures and callers without the read stay valid. */
  listing?: ListingSummary | null;
  listing_fetched_at?: string | null;
  creators: string[];
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
  /** The batch this came from and when it was last imported, for the filters.
      Null on rows imported before these were recorded, or pasted with no file. */
  import_filename: string | null;
  imported_at: string | null;
  /** Library assets linked from a filled creative. Optional for older payloads. */
  creative_assets?: { asset_id: string; draft_id: string; position: number }[];
  /** Drafts queued for this product, including ones still pending. */
  creative_drafts?: {
    id: string;
    kind: string;
    recipe: string;
    status: string;
    card_count: number;
    owed: number;
  }[];
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
