/**
 * PlanPerm’s ArcGIS adapter: a fresh site coordinate returns nearby applications and explicit summary metrics.
 * Keep raw public data normalization on the server so each client pin follows the same evidence contract.
 */
export type PlanningDecision = "GRANTED" | "REFUSED" | "PENDING" | "WITHDRAWN";

export type NearbyApplication = {
  applicationRef: string;
  council: string;
  address: string;
  lat: number;
  lng: number;
  applicationType: string;
  description: string;
  receivedDate: string | null;
  decidedDate: string | null;
  decision: PlanningDecision;
  link: string;
  distanceKm: number;
};

const ARCGIS_URL = "https://services.arcgis.com/NzlPQPKn5QF9v2US/arcgis/rest/services/Planning_Applications_Ireland_PreProd/FeatureServer/0/query";
const ARCGIS_LAYER_URL = "https://services.arcgis.com/NzlPQPKn5QF9v2US/arcgis/rest/services/Planning_Applications_Ireland_PreProd/FeatureServer/0";
const OUT_FIELDS = ["OBJECTID", "PlanningAuthority", "ApplicationNumber", "DevelopmentDescription", "DevelopmentAddress", "ApplicationType", "ApplicationStatus", "Decision", "ReceivedDate", "DecisionDate", "LinkAppDetails"];

export function sourceUrlForApplication(recordLink: unknown, objectId: unknown) {
  if (typeof recordLink === "string" && recordLink.trim()) return recordLink.trim();
  return typeof objectId === "number" || typeof objectId === "string" ? `${ARCGIS_LAYER_URL}/${objectId}?f=html` : ARCGIS_LAYER_URL;
}

export function normalizeDecision(raw: string | null | undefined): PlanningDecision {
  const value = (raw ?? "").trim().toUpperCase();
  if (!value || value === "N/A") return "PENDING";
  if (["REFUS", "REJECT", "INVALID"].some(term => value.includes(term))) return "REFUSED";
  if (["GRANT", "APPROVE", "CONDITIONAL", "UNCONDITIONAL", "SPLIT DECISION"].some(term => value.includes(term))) return "GRANTED";
  if (value.includes("WITHDRAW")) return "WITHDRAWN";
  return "PENDING";
}

export function distanceKm(from: { lat: number; lng: number }, to: { lat: number; lng: number }) {
  const radians = (value: number) => value * Math.PI / 180;
  const earthRadius = 6371;
  const dLat = radians(to.lat - from.lat);
  const dLng = radians(to.lng - from.lng);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(radians(from.lat)) * Math.cos(radians(to.lat)) * Math.sin(dLng / 2) ** 2;
  return earthRadius * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function epochToDate(value: unknown): string | null {
  if (typeof value !== "number") return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString().slice(0, 10);
}

export function makePlanningSummary(applications: NearbyApplication[]) {
  const decided = applications.filter(app => app.decision === "GRANTED" || app.decision === "REFUSED");
  const granted = applications.filter(app => app.decision === "GRANTED").length;
  const refused = applications.filter(app => app.decision === "REFUSED").length;
  const pending = applications.filter(app => app.decision === "PENDING").length;
  return {
    total: applications.length,
    granted,
    refused,
    pending,
    approvalRate: decided.length ? Math.round((granted / decided.length) * 100) : null,
  };
}

export async function fetchNearbyPlanningApplications({ lat, lng, radiusKm }: { lat: number; lng: number; radiusKm: number }) {
  const latitudeOffset = radiusKm / 111;
  const longitudeOffset = radiusKm / (111 * Math.cos(lat * Math.PI / 180));
  const params = new URLSearchParams({
    f: "json",
    geometry: `${lng - longitudeOffset},${lat - latitudeOffset},${lng + longitudeOffset},${lat + latitudeOffset}`,
    geometryType: "esriGeometryEnvelope",
    inSR: "4326",
    outSR: "4326",
    spatialRel: "esriSpatialRelIntersects",
    outFields: OUT_FIELDS.join(","),
    returnGeometry: "true",
    resultRecordCount: "250",
    orderByFields: "OBJECTID DESC",
  });
  const response = await fetch(`${ARCGIS_URL}?${params.toString()}`, { signal: AbortSignal.timeout(20_000) });
  if (!response.ok) throw new Error(`Planning data request failed (${response.status})`);
  const data = await response.json() as { error?: { message?: string }; features?: Array<{ attributes?: Record<string, unknown>; geometry?: { x?: number; y?: number } }> };
  if (data.error) throw new Error(data.error.message ?? "Planning data service returned an error");
  const origin = { lat, lng };
  const applications = (data.features ?? []).flatMap(feature => {
    const attrs = feature.attributes ?? {};
    const point = feature.geometry;
    if (typeof point?.x !== "number" || typeof point.y !== "number") return [];
    const application: NearbyApplication = {
      applicationRef: String(attrs.ApplicationNumber ?? ""),
      council: String(attrs.PlanningAuthority ?? ""),
      address: String(attrs.DevelopmentAddress ?? ""),
      lat: point.y,
      lng: point.x,
      applicationType: String(attrs.ApplicationType ?? ""),
      description: String(attrs.DevelopmentDescription ?? ""),
      receivedDate: epochToDate(attrs.ReceivedDate),
      decidedDate: epochToDate(attrs.DecisionDate),
      decision: normalizeDecision(typeof attrs.Decision === "string" ? attrs.Decision : ""),
      link: sourceUrlForApplication(attrs.LinkAppDetails, attrs.OBJECTID),
      distanceKm: Number(distanceKm(origin, { lat: point.y, lng: point.x }).toFixed(2)),
    };
    return application.distanceKm <= radiusKm ? [application] : [];
  });
  return { applications, summary: makePlanningSummary(applications) };
}
