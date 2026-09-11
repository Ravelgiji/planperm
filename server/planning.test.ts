import { describe, expect, it } from "vitest";
import { distanceKm, makePlanningSummary, normalizeDecision, sourceUrlForApplication, type NearbyApplication } from "./planning";

describe("PlanPerm planning adapter", () => {
  it("normalizes planning outcomes with refusal taking precedence", () => {
    expect(normalizeDecision("Grant Permission")).toBe("GRANTED");
    expect(normalizeDecision("Refuse conditional permission")).toBe("REFUSED");
    expect(normalizeDecision("Withdrawn")).toBe("WITHDRAWN");
    expect(normalizeDecision("")).toBe("PENDING");
  });

  it("summarizes a freshly selected site’s nearby applications", () => {
    const apps = ["GRANTED", "GRANTED", "REFUSED", "PENDING"].map((decision, index) => ({ applicationRef: String(index), council: "", address: "", lat: 53.3, lng: -6.2, applicationType: "", description: "", receivedDate: null, decidedDate: null, decision: decision as NearbyApplication["decision"], link: "", distanceKm: 0.2 }));
    expect(makePlanningSummary(apps)).toMatchObject({ total: 4, granted: 2, refused: 1, pending: 1, approvalRate: 67 });
  });

  it("calculates a non-zero distance when a new pin is moved", () => {
    expect(distanceKm({ lat: 53.3237, lng: -6.2644 }, { lat: 53.3337, lng: -6.2644 })).toBeGreaterThan(1);
  });

  it("uses the supplied application record link or a direct ArcGIS feature fallback", () => {
    expect(sourceUrlForApplication("https://planning.example/application/12", 12)).toBe("https://planning.example/application/12");
    expect(sourceUrlForApplication("", 42)).toContain("/FeatureServer/0/42?f=html");
  });
});
