const API_URL = process.env.API_URL;

export const hasApi = Boolean(API_URL);

// On Cloud Run the api is private, so calls carry an identity token from the metadata server
async function authHeaders(): Promise<Record<string, string>> {
  if (!process.env.K_SERVICE || !API_URL) return {};
  const res = await fetch(
    `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${API_URL}`,
    { headers: { "Metadata-Flavor": "Google" } },
  );
  return { Authorization: `Bearer ${await res.text()}` };
}

export async function apiFetch(path: string, init: RequestInit = {}) {
  if (!API_URL) throw new Error("API_URL is not set");
  return fetch(`${API_URL}${path}`, { ...init, headers: { ...init.headers, ...(await authHeaders()) } });
}
