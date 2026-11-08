import { getIdentity } from "../../../cursor/identity";
import { MODEL } from "../../../lib/schema";
import { anthropicApiKey, buildingGeocoderApiKey, buildingRegisterApiKey, json } from "../../../lib/http";
import { csrfCookieHeader, mintCsrf } from "../../../lib/csrf";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const identity = await getIdentity(request);
  const hasKey = Boolean(anthropicApiKey());
  const viewer = identity.verified
    ? identity.kind === "person"
      ? identity.name || "사용자"
      : identity.name || "API 키"
    : "";
  const csrf = mintCsrf();
  return json(
    {
      model: MODEL,
      mock: !hasKey,
      buildingRegister: Boolean(buildingRegisterApiKey()),
      buildingGeocoder: Boolean(buildingGeocoderApiKey()),
      viewer,
      signedIn: identity.verified && identity.kind === "person",
      simulated: identity.verified && identity.simulated === true,
      csrf,
    },
    200,
    { "set-cookie": csrfCookieHeader(csrf, request) },
  );
}
