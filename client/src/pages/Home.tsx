/** Map-first planning workspace: selection drives evidence, map points open source-backed application detail. */
import { useEffect, useMemo, useRef, useState } from "react";
import { trpc } from "@/lib/trpc";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { MapView } from "@/components/Map";
import {
  ArrowUpRight, Bot, Building2, CalendarDays, ChevronDown, CircleHelp, Clock3, ExternalLink,
  MapPin, Menu, MousePointer2, Navigation, PanelRight, Search, Send, Sparkles, X,
} from "lucide-react";

const TERRAIN = "/manus-storage/planperm-hero-terrain_5b970fa2.jpg";
const CONTOURS = "/manus-storage/planperm-insight-contours_ee015cc1.jpg";
const LOGO = "/manus-storage/planperm-logo-mark_246f7cb3.png";

type Decision = "GRANTED" | "REFUSED" | "PENDING" | "WITHDRAWN";
type MapMode = "explore" | "pin";
type PinnedSite = { label: string; source: "Search result" | "Map pin"; position: google.maps.LatLngLiteral; requestId: number };
type Application = {
  applicationRef: string; council: string; address: string; lat: number; lng: number;
  applicationType: string; description: string; receivedDate: string | null; decidedDate: string | null;
  decision: Decision; link: string; distanceKm: number;
};
type ChatMessage = { id: string; role: "assistant" | "user"; content: string };

const trendBars = [44, 54, 48, 67, 71, 76, 64, 78, 83, 74, 86, 91];

function DecisionBadge({ decision }: { decision: Decision }) {
  return <span className={`decision-badge decision-${decision.toLowerCase()}`}>{decision.charAt(0) + decision.slice(1).toLowerCase()}</span>;
}

function Metric({ value, label, note, tone = "teal" }: { value: string; label: string; note: string; tone?: "teal" | "navy" | "clay" | "stone" }) {
  return <article className={`metric-card metric-${tone}`}><div className="metric-card__top"><span>{label}</span><ArrowUpRight aria-hidden="true" size={15} /></div><strong>{value}</strong><small>{note}</small></article>;
}

function formatDate(value: string | null) {
  if (!value) return "Not recorded";
  return new Intl.DateTimeFormat("en-IE", { day: "numeric", month: "short", year: "numeric" }).format(new Date(`${value}T12:00:00Z`));
}

export default function Home() {
  const mapRef = useRef<google.maps.Map | null>(null);
  const siteMarkerRef = useRef<google.maps.marker.AdvancedMarkerElement | null>(null);
  const applicationMarkersRef = useRef<google.maps.Marker[]>([]);
  const [activeTab, setActiveTab] = useState("Overview");
  const [radius, setRadius] = useState(2);
  const [question, setQuestion] = useState("");
  const [locationQuery, setLocationQuery] = useState("");
  const [mapMode, setMapMode] = useState<MapMode>("explore");
  const [pinnedSite, setPinnedSite] = useState<PinnedSite | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [searchStatus, setSearchStatus] = useState<"idle" | "loading" | "error">("idle");
  const [selectedApplication, setSelectedApplication] = useState<Application | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);

  const siteQuery = useMemo(() => pinnedSite ? ({ siteLabel: pinnedSite.label, lat: pinnedSite.position.lat, lng: pinnedSite.position.lng, radiusKm: radius, requestId: pinnedSite.requestId }) : undefined, [pinnedSite, radius]);
  const planningQuery = trpc.planning.nearby.useQuery(siteQuery as { siteLabel: string; lat: number; lng: number; radiusKm: number; requestId: number }, { enabled: Boolean(siteQuery), staleTime: 0, retry: 1, refetchOnWindowFocus: false });
  const planning = planningQuery.data;
  const applications = (planning?.applications ?? []) as Application[];

  function clearApplicationMarkers() {
    applicationMarkersRef.current.forEach(marker => { marker.setMap(null); });
    applicationMarkersRef.current = [];
  }

  function renderSiteMarker(position: google.maps.LatLngLiteral) {
    const map = mapRef.current;
    if (!map || !window.google?.maps?.marker) return;
    if (siteMarkerRef.current) { siteMarkerRef.current.position = position; return; }
    const marker = document.createElement("div");
    marker.className = "site-marker";
    marker.innerHTML = '<span class="site-marker__halo"></span><span class="site-marker__core"></span>';
    siteMarkerRef.current = new window.google.maps.marker.AdvancedMarkerElement({ map, position, content: marker, title: "Selected planning site" });
  }

  useEffect(() => {
    clearApplicationMarkers();
    if (!mapReady || !mapRef.current || !window.google?.maps?.Marker) return;
    const markerDomCleanups: Array<() => void> = [];
    const markerDomTimers: number[] = [];
    const markerApplications = new Map<string, Application>();
    const openRenderedMarker = (event: Event) => {
      const target = event.target instanceof Element ? event.target.closest<HTMLElement>('[role="button"][title]') : null;
      const application = target ? markerApplications.get(target.getAttribute("title") ?? "") : undefined;
      if (application) setSelectedApplication(application);
    };
    document.addEventListener("click", openRenderedMarker, true);
    document.addEventListener("pointerup", openRenderedMarker, true);
    applicationMarkersRef.current = applications.slice(0, 120).map(application => {
      const colorByDecision: Record<Decision, string> = { GRANTED: "#0a7666", REFUSED: "#b4634b", PENDING: "#bc882f", WITHDRAWN: "#74878d" };
      const markerTitle = `${application.decision}: ${application.address || application.applicationRef} · ${application.applicationRef}`;
      markerApplications.set(markerTitle, application);
      const marker = new window.google.maps.Marker({
        map: mapRef.current!,
        position: { lat: application.lat, lng: application.lng },
        title: markerTitle,
        icon: { path: window.google.maps.SymbolPath.CIRCLE, fillColor: colorByDecision[application.decision], fillOpacity: 1, strokeColor: "#ffffff", strokeOpacity: 1, strokeWeight: 2, scale: 7 },
        zIndex: 2,
      });
      const openApplicationDetail = () => setSelectedApplication(application);
      marker.addListener("click", openApplicationDetail);
      markerDomTimers.push(window.setTimeout(() => {
        const renderedMarker = Array.from(document.querySelectorAll<HTMLElement>('[role="button"][title]')).find(element => element.getAttribute("title") === markerTitle);
        if (!renderedMarker) return;
        renderedMarker.addEventListener("click", openApplicationDetail);
        renderedMarker.addEventListener("pointerup", openApplicationDetail);
        markerDomCleanups.push(() => {
          renderedMarker.removeEventListener("click", openApplicationDetail);
          renderedMarker.removeEventListener("pointerup", openApplicationDetail);
        });
      }, 100));
      return marker;
    });
    return () => {
      markerDomTimers.forEach(window.clearTimeout);
      markerDomCleanups.forEach(cleanup => cleanup());
      document.removeEventListener("click", openRenderedMarker, true);
      document.removeEventListener("pointerup", openRenderedMarker, true);
      clearApplicationMarkers();
    };
  }, [mapReady, applications]);

  function selectSite(site: Omit<PinnedSite, "requestId">) {
    setPinnedSite({ ...site, requestId: Date.now() });
    setMapMode("explore");
    setSelectedApplication(null);
    setChatMessages([{ id: "intro", role: "assistant", content: `You are viewing ${site.label}. Ask a planning question when the AI service is connected.` }]);
    if (mapRef.current) { mapRef.current.panTo(site.position); mapRef.current.setZoom(16); renderSiteMarker(site.position); }
  }

  function handleMapReady(map: google.maps.Map) {
    mapRef.current = map;
    setMapReady(true);
    map.setOptions({ mapTypeControl: false, streetViewControl: false, fullscreenControl: false, clickableIcons: false, gestureHandling: "greedy" });
  }

  function searchLocation(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const term = locationQuery.trim();
    if (!term || !mapRef.current || !window.google) return;
    setSearchStatus("loading");
    new window.google.maps.Geocoder().geocode({ address: term.toLowerCase().includes("ireland") ? term : `${term}, Ireland` }, (results, status) => {
      if (status !== "OK" || !results?.[0]) { setSearchStatus("error"); return; }
      const result = results[0];
      const location = result.geometry.location;
      selectSite({ label: result.formatted_address.replace(", Ireland", ""), source: "Search result", position: { lat: location.lat(), lng: location.lng() } });
      setSearchStatus("idle");
    });
  }

  function sendQuestion() {
    const content = question.trim();
    if (!content) return;
    setChatMessages(current => [...current, { id: `user-${Date.now()}`, role: "user", content }, { id: `future-${Date.now()}`, role: "assistant", content: "The chat interface is ready. Connect the PlanPerm AI endpoint here to return a grounded response." }]);
    setQuestion("");
  }

  const selectedSiteStatus = planningQuery.isFetching ? "Updating nearby records" : planningQuery.isError ? "Could not load the planning record" : `${planning?.summary.total ?? 0} nearby applications`;

  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="#workspace" aria-label="PlanPerm workspace"><img src={LOGO} alt="" className="brand__mark" /><span className="brand__word">plan<span>perm</span></span></a><nav className="topnav" aria-label="Primary navigation"><a className="topnav__link topnav__link--active" href="#workspace">Workspace</a><a className="topnav__link" href="#precedents">Precedents</a><a className="topnav__link" href="#method">How it works</a></nav><div className="topbar__actions"><button className="icon-button icon-button--quiet" aria-label="Help"><CircleHelp size={18} /></button><button className="avatar" aria-label="Account menu">FI</button><button className="mobile-menu icon-button icon-button--quiet" aria-label="Open menu"><Menu size={20} /></button></div></header>
    <main id="workspace" className="workspace">
      <section className="placebar" aria-label="Area controls"><div className="placebar__eyebrow"><span className={pinnedSite ? "signal-dot" : "signal-dot signal-dot--quiet"} /> {pinnedSite ? selectedSiteStatus : "Start with a site"}</div><div className="placebar__row"><button className="site-status-control" onClick={() => document.getElementById("site-map")?.scrollIntoView({ behavior: "smooth" })}><MapPin size={17} /><span>{pinnedSite ? pinnedSite.label : "Search or pin a location"}</span><ChevronDown size={16} /></button><button className="scope-control" onClick={() => setRadius(current => current === 2 ? 1 : 2)}><span>Search radius</span><strong>{radius} km</strong><ChevronDown size={15} /></button><span className="preview-pill"><Sparkles size={14} /> Live planning query</span></div></section>
      <section className="page-heading"><div><p className="section-kicker"><span className="kicker-rule" /> {pinnedSite ? "Selected site" : "Pin your proposal"}</p><h1>{pinnedSite ? <>Read the place.<br /><em>Plan the next move.</em></> : <>Place a pin. Read<br /><em>the local record.</em></>}</h1></div><div className="source-stamp"><span className="source-stamp__label">Source framework</span><strong>MyPlan.ie / ArcGIS</strong><span>{pinnedSite ? "Every new pin triggers a new nearby-application request." : "Search for an Irish place or click the map before asking PlanPerm a question."}</span></div></section>
      <section className="metrics-grid" aria-label="Area metrics"><Metric value={pinnedSite ? String(planning?.summary.total ?? "—") : "—"} label="Applications" note={pinnedSite ? "within the selected radius" : "select a site to load"} tone="navy" /><Metric value={pinnedSite && planning?.summary.approvalRate !== null && planning ? `${planning.summary.approvalRate}%` : "—"} label="Approval rate" note={pinnedSite ? "granted decisions only" : "local result pending"} tone="teal" /><Metric value={pinnedSite ? `${radius} km` : "—"} label="Search area" note={pinnedSite ? "around the site pin" : "local result pending"} tone="stone" /><Metric value={pinnedSite && planning ? String(planning.summary.refused) : "—"} label="Refusals" note={pinnedSite ? "nearby applications" : "local result pending"} tone="clay" /></section>
      <section className="analysis-grid"><article className="map-panel"><div className="map-panel__toolbar"><div className="segmented" role="tablist" aria-label="Workspace views">{["Overview", "Applications", "Trends"].map(tab => <button key={tab} className={activeTab === tab ? "is-active" : ""} onClick={() => setActiveTab(tab)} role="tab" aria-selected={activeTab === tab}>{tab}</button>)}</div><span className="map-interaction-label"><MousePointer2 size={15} /> Select a point for detail</span></div><div id="site-map" className="map-canvas map-canvas--live" style={{ backgroundImage: `url(${TERRAIN})` }}><MapView className="planperm-map" initialCenter={{ lat: 53.3237, lng: -6.2644 }} initialZoom={13} onMapReady={handleMapReady} onMapClick={position => selectSite({ label: "Selected map point", source: "Map pin", position })} />{!mapReady && <div className="map-loading"><span className="signal-dot" /> Loading the location canvas</div>}<div className="map-interface"><form className="map-search-bar" onSubmit={searchLocation}><Search size={17} /><input value={locationQuery} onChange={event => setLocationQuery(event.target.value)} placeholder="Search a town, Eircode, or address" aria-label="Search a town, Eircode, or address" /><button type="submit" disabled={!locationQuery.trim() || searchStatus === "loading"}>{searchStatus === "loading" ? "Searching…" : "Find"}</button></form>{searchStatus === "error" && <p className="map-search-error">That location could not be found. Try a town, Eircode, or address in Ireland.</p>}<div className="map-mode-controls"><button className={mapMode === "explore" ? "is-active" : ""} onClick={() => setMapMode("explore")}><Navigation size={15} /> Explore</button><button className={mapMode === "pin" ? "is-active" : ""} onClick={() => setMapMode("pin")}><MapPin size={15} /> Drop pin</button></div>{mapMode === "pin" && <div className="map-placement-tip"><MapPin size={17} /><span><b>Choose the site</b>Click the property on the map.</span><button onClick={() => setMapMode("explore")} aria-label="Cancel placement"><X size={15} /></button></div>}</div>{pinnedSite ? <div className="map-site-card"><div className="map-site-card__top"><span className="site-selected-label"><span className="signal-dot" /> Site pinned</span><button onClick={() => { setPinnedSite(null); clearApplicationMarkers(); }} aria-label="Clear selected site"><X size={16} /></button></div><strong>{pinnedSite.label}</strong><p>{selectedSiteStatus} · {radius} km radius</p><button className="text-link" onClick={() => document.getElementById("assistant-rail")?.scrollIntoView({ behavior: "smooth" })}>Open site assistant <ArrowUpRight size={14} /></button></div> : <div className="map-guide-card"><span className="map-guide-card__number">01</span><div><strong>Start with the exact site</strong><p>Search above or click directly on the map. Nearby applications and the assistant appear after you choose your point.</p></div></div>}<div className="map-key"><span><i className="key-dot key-dot--granted" />Selected site</span><span><i className="key-dot key-dot--pending" />Application points</span></div></div><div className="map-panel__footer"><div><span className="footer-label">Site interaction</span><strong>{pinnedSite ? "Click a nearby point to read its record" : "Choose a point to unlock the assessment"}</strong></div><button className="outline-button" onClick={() => setMapMode("pin")}><MapPin size={16} /> {pinnedSite ? "Move site pin" : "Drop a pin"}</button></div></article>
      <aside id="assistant-rail" className="intelligence-rail">{pinnedSite ? <article className="assistant-rail"><div className="assistant-rail__head"><span className="card-eyebrow"><Bot size={14} /> PlanPerm assistant</span><span className="grounded-pill"><span /> Chat preview</span></div><div className="assistant-rail__site"><MapPin size={15} /><span>{pinnedSite.label}</span></div><div className="chat-thread" aria-label="PlanPerm chat preview">{chatMessages.map(message => <div className={`chat-bubble chat-bubble--${message.role}`} key={message.id}>{message.role === "assistant" && <img src={LOGO} alt="" />}{message.content}</div>)}</div><div className="chat-composer"><input value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === "Enter") sendQuestion(); }} placeholder="Message PlanPerm…" aria-label="Message PlanPerm" /><button onClick={sendQuestion} aria-label="Send message"><Send size={17} /></button></div><p className="chat-preview-note">UI preview only — connect the PlanPerm AI service when ready.</p></article> : <article className="rail-empty-state"><span className="rail-empty-state__step">02</span><div className="rail-empty-state__icon"><Bot size={23} /></div><h2>PlanPerm waits<br />for your site.</h2><p>Choose a location to unlock the chat interface alongside the planning record.</p><button className="dark-button" onClick={() => setMapMode("pin")}><MapPin size={16} /> Select on map</button><span className="rail-empty-state__hint">Search works too</span></article>}</aside></section>
      {pinnedSite && <section id="precedents" className="precedents-section"><div className="precedents-section__intro" style={{ backgroundImage: `linear-gradient(90deg, rgba(244,239,226,.97), rgba(244,239,226,.72)), url(${CONTOURS})` }}><p className="section-kicker"><span className="kicker-rule" /> Site record</p><h2>Nearby decisions,<br /><em>mapped to your pin.</em></h2><p>{planningQuery.isFetching ? "Refreshing the public planning record for this selected point." : "Select an application point or a listed record to inspect its details and original source."}</p><button className="dark-button" onClick={() => setActiveTab("Applications")}>Review applications <ArrowUpRight size={16} /></button></div><div className="precedent-data"><div className="trend-card"><div className="trend-card__head"><div><span className="card-eyebrow">Search coverage</span><h3>{planning?.summary.total ?? "—"} mapped applications</h3></div><span className="year-select">{radius} km <ChevronDown size={14} /></span></div><div className="bar-plot" aria-label="Illustrative record-density graphic">{trendBars.map((height, index) => <span className={index > 8 ? "bar-plot__bar bar-plot__bar--highlight" : "bar-plot__bar"} style={{ height: `${height}%` }} key={index} />)}</div><div className="plot-axis"><span>Near</span><span>Radius</span><span>Coverage</span><span>Source</span><span>Pin</span></div><p className="trend-caption"><MapPin size={15} /> Points are recalculated around the selected site.</p><div className="evidence-line"><span><MapPin size={13} /> {pinnedSite.label}</span><span>ArcGIS record</span></div></div><div className="records-card"><div className="records-card__head"><div><span className="card-eyebrow">Nearby applications</span><h3>{planningQuery.isFetching ? "Loading records" : `${applications.length} applications`}</h3></div><button className="icon-button icon-button--soft" aria-label="Open applications panel"><PanelRight size={16} /></button></div><div className="record-list">{applications.slice(0, 4).map(application => <button className="record-row" onClick={() => setSelectedApplication(application)} key={`${application.applicationRef}-${application.lat}`}><DecisionBadge decision={application.decision} /><span><strong>{application.address || application.applicationRef || "Planning application"}</strong><small>{application.applicationType || "Application"} · {application.distanceKm} km away</small></span><ArrowUpRight size={16} /></button>)}{!planningQuery.isFetching && applications.length === 0 && <p className="records-empty">No mapped applications were returned in this radius. Try a larger radius or another nearby point.</p>}</div><div className="records-card__footnote"><Clock3 size={13} /> Fresh query for this site pin</div></div></div></section>}
    </main><footer id="method" className="footer"><span className="footer__brand"><img src={LOGO} alt="" /> plan<span>perm</span></span><span>Local evidence, made legible.</span><span className="footer__last"><Building2 size={14} /> Planning intelligence for Ireland</span></footer>
    <Dialog open={Boolean(selectedApplication)} onOpenChange={open => { if (!open) setSelectedApplication(null); }}><DialogContent className="application-dialog"><DialogHeader><span className="application-dialog__eyebrow">Planning application</span><DialogTitle>{selectedApplication?.address || selectedApplication?.applicationRef || "Application detail"}</DialogTitle><DialogDescription>{selectedApplication?.applicationRef || "Reference unavailable"} · {selectedApplication?.council || "Planning authority"}</DialogDescription></DialogHeader>{selectedApplication && <div className="application-dialog__body"><div className="application-dialog__decision"><DecisionBadge decision={selectedApplication.decision} /><span>{selectedApplication.distanceKm} km from your selected site</span></div><dl className="application-dialog__facts"><div><dt>Application type</dt><dd>{selectedApplication.applicationType || "Not recorded"}</dd></div><div><dt>Received</dt><dd><CalendarDays size={14} /> {formatDate(selectedApplication.receivedDate)}</dd></div><div><dt>Decision date</dt><dd><CalendarDays size={14} /> {formatDate(selectedApplication.decidedDate)}</dd></div></dl><div className="application-dialog__description"><span>Proposal</span><p>{selectedApplication.description || "No proposal description was published with this record."}</p></div><a className="application-dialog__source" href={selectedApplication.link} target="_blank" rel="noreferrer">Open original application source <ExternalLink size={15} /></a></div>}</DialogContent></Dialog>
  </div>;
}
