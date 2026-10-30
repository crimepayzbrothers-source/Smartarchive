import { useCallback, useEffect, useState } from "react";
import { get, post, setUnauthorizedHandler } from "./api";
import { MetaProvider, ToastProvider, go, useRoute, usePoll } from "./lib";
import UploadSheet from "./components/UploadSheet";
import { Icon, Loading } from "./components/ui";
import Archive from "./pages/Archive";
import DocumentPage from "./pages/DocumentPage";
import Expenses from "./pages/Expenses";
import Home from "./pages/Home";
import Inbox from "./pages/Inbox";
import Transactions from "./pages/Transactions";
import { TenancyPage, TenantsPage } from "./pages/Tenants";
import { BkOverview, BkWizard } from "./pages/Betriebskosten";
import Login from "./pages/Login";
import { Properties, PropertyPage } from "./pages/Properties";
import Settings from "./pages/Settings";

type Status = { setup_required: boolean; setup_possible: boolean; user: any; ai_available: boolean };

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const check = useCallback(() => {
    get<Status>("/api/auth/status").then((s) => { setStatus(s); setFailed(null); }).catch((e) => setFailed(e.message));
  }, []);
  useEffect(() => {
    check();
    setUnauthorizedHandler(() => setStatus((s) => (s ? { ...s, user: null } : s)));
  }, [check]);

  if (failed) return <main className="login"><div className="login-card"><h1>SmartArchive</h1><p className="notice error">{failed}</p><button className="primary" onClick={check}>Erneut versuchen</button></div></main>;
  if (!status) return <Loading />;
  if (!status.user) return <ToastProvider><Login setupRequired={status.setup_required} setupPossible={status.setup_possible} onDone={check} /></ToastProvider>;
  return (
    <ToastProvider>
      <MetaProvider>
        <Shell user={status.user} onLogout={async () => { await post("/api/auth/logout"); check(); }} />
      </MetaProvider>
    </ToastProvider>
  );
}

const NAV = [
  { path: "/", label: "Start", icon: "home" },
  { path: "/eingang", label: "Eingang", icon: "inbox" },
  { path: "/archiv", label: "Archiv", icon: "archive" },
  { path: "/objekte", label: "Objekte", icon: "building" },
  { path: "/ausgaben", label: "Ausgaben", icon: "euro" },
];

function Shell({ user, onLogout }: { user: any; onLogout: () => void }) {
  const [path, params] = useRoute();
  const [capture, setCapture] = useState(false);
  const [badge, setBadge] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [shared, setShared] = useState<File[] | undefined>(undefined);

  // Über „Teilen“ aus anderen Apps empfangene Dateien (vom Service Worker zwischengelegt)
  useEffect(() => {
    if (path !== "/teilen" || !("caches" in window)) return;
    (async () => {
      const cache = await caches.open("sa-share");
      const files: File[] = [];
      for (const req of await cache.keys()) {
        const res = await cache.match(req);
        if (!res) continue;
        const blob = await res.blob();
        files.push(new File([blob], decodeURIComponent(res.headers.get("X-Filename") || "geteilt"), { type: blob.type }));
        await cache.delete(req);
      }
      go("/eingang");
      if (files.length) { setShared(files); setCapture(true); }
    })();
  }, [path]);
  const loadBadge = useCallback(() => get("/api/dashboard").then((d) => setBadge(d.counts.inbox + d.counts.to_review + d.counts.error + (d.counts.transactions || 0) + (d.counts.undated || 0))).catch(() => {}), []);
  useEffect(() => { loadBadge(); }, [loadBadge, path, refresh]);
  usePoll(true, loadBadge, 20000);

  const section = "/" + (path.split("/")[1] || "");
  const active = (p: string) => (p === "/" ? section === "/" : section === p || (p === "/archiv" && (section === "/dokument" || section === "/suche")) || (p === "/objekte" && (section === "/objekt" || section === "/mieter" || section === "/betriebskosten")) || (p === "/ausgaben" && section === "/umsaetze"));
  const area = path.startsWith("/archiv") && params.get("bereich") === "vermietung" ? "vermietung" : "auto";

  let page;
  const m = path.match(/^\/(dokument|objekt|mieter|betriebskosten)\/(\d+)$/);
  if (m && m[1] === "dokument") page = <DocumentPage id={Number(m[2])} />;
  else if (m && m[1] === "mieter") page = <TenancyPage id={Number(m[2])} />;
  else if (m && m[1] === "betriebskosten") page = <BkWizard key={m[2]} id={Number(m[2])} params={params} />;
  else if (m && m[1] === "objekt") page = <PropertyPage id={Number(m[2])} />;
  else if (path === "/eingang") page = <Inbox key={refresh} onCapture={() => setCapture(true)} />;
  else if (path === "/archiv") page = <Archive key="archiv" params={params} />;
  else if (path === "/suche") page = <Archive key="suche" params={params} searchMode />;
  else if (path === "/objekte") page = <Properties />;
  else if (path === "/ausgaben") page = <Expenses params={params} />;
  else if (path === "/umsaetze") page = <Transactions params={params} />;
  else if (path === "/mieter") page = <TenantsPage params={params} />;
  else if (path === "/betriebskosten") page = <BkOverview params={params} />;
  else if (path === "/einstellungen") page = <Settings user={user} onLogout={onLogout} />;
  else page = <Home key={refresh} onCapture={() => setCapture(true)} />;

  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="#/"><span className="brand-mark" aria-hidden="true"><span /><span /><span /></span> SmartArchive</a>
        <button className="primary capture-side" onClick={() => setCapture(true)}><Icon name="camera" size={20} /> Erfassen</button>
        <nav>
          {NAV.map((n) => (
            <a key={n.path} href={`#${n.path}`} className={active(n.path) ? "on" : ""}>
              <Icon name={n.icon} /> {n.label}
              {n.path === "/eingang" && badge > 0 && <span className="badge">{badge}</span>}
            </a>
          ))}
          <a href="#/suche" className={section === "/suche" ? "on" : ""}><Icon name="search" /> Suche</a>
        </nav>
        <a href="#/einstellungen" className={`settings-link ${section === "/einstellungen" ? "on" : ""}`}><Icon name="settings" /> Einstellungen</a>
      </aside>

      <header className="topbar">
        <a className="brand" href="#/"><span className="brand-mark" aria-hidden="true"><span /><span /><span /></span> SmartArchive</a>
        <div className="topbar-actions">
          <button className="icon-btn" aria-label="Suche" onClick={() => go("/suche")}><Icon name="search" /></button>
          <button className="icon-btn" aria-label="Einstellungen" onClick={() => go("/einstellungen")}><Icon name="settings" /></button>
        </div>
      </header>

      <main className="content">{page}</main>

      <nav className="bottomnav" aria-label="Hauptnavigation">
        {NAV.slice(1, 3).map((n) => (
          <a key={n.path} href={`#${n.path}`} className={active(n.path) ? "on" : ""}>
            <Icon name={n.icon} /><span>{n.label}</span>
            {n.path === "/eingang" && badge > 0 && <span className="badge">{badge}</span>}
          </a>
        ))}
        <button className="fab" onClick={() => setCapture(true)} aria-label="Beleg erfassen"><Icon name="camera" size={26} /></button>
        {NAV.slice(3, 5).map((n) => (
          <a key={n.path} href={`#${n.path}`} className={active(n.path) ? "on" : ""}><Icon name={n.icon} /><span>{n.label}</span></a>
        ))}
      </nav>

      <UploadSheet open={capture} onClose={() => { setCapture(false); setShared(undefined); }} defaultArea={area} initialFiles={shared}
        onDone={() => setRefresh((r) => r + 1)} />
    </div>
  );
}
