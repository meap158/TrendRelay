const configuredApiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export function apiBaseUrl(): string {
  const url = new URL(configuredApiUrl);
  if (
    typeof window !== "undefined" &&
    ["localhost", "127.0.0.1", "0.0.0.0"].includes(url.hostname)
  ) {
    url.hostname = window.location.hostname;
  }
  return url.origin;
}
