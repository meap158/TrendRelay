import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | null | undefined;

export function authConfiguration() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  return { configured: Boolean(url && publishableKey), url, publishableKey };
}

export function supabaseBrowserClient(): SupabaseClient | null {
  if (client !== undefined) return client;
  const config = authConfiguration();
  client = config.configured
    ? createClient(config.url!, config.publishableKey!, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: true,
          flowType: "pkce",
        },
      })
    : null;
  return client;
}
