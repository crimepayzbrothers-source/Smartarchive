import { get, money } from "../api";
import { useLoad, usePoll } from "../lib";
import { DocRow, Empty, ErrorBox, Icon, Loading } from "../components/ui";

export default function Home({ onCapture }: { onCapture: () => void }) {
  const { data, error, loading, reload } = useLoad(() => get("/api/dashboard"), []);
  usePoll(Boolean(data?.counts.processing), reload);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox text={error} />;
  const c = data.counts;
  const exp = data.expenses;
  const waiting = c.inbox + c.to_review + c.error + (c.transactions || 0) + (c.undated || 0);

  return (
    <div className="page home">
      <section className="capture-hero">
        <div>
          <h1>{waiting ? `${waiting} ${waiting === 1 ? "Eintrag wartet" : "Einträge warten"} auf Sie` : "Alles abgelegt"}</h1>
          <p className="lead">{data.counts.filed} {data.counts.filed === 1 ? "Dokument" : "Dokumente"} im Archiv, davon {data.per_area.privat || 0} privat und {data.per_area.vermietung || 0} zur Vermietung.</p>
        </div>
        <button className="primary capture" onClick={onCapture}><Icon name="camera" size={22} /> Beleg erfassen</button>
      </section>

      {(c.inbox > 0 || c.to_review > 0 || c.error > 0 || c.processing > 0 || c.transactions > 0 || c.undated > 0) && (
        <section className="status-strip">
          {c.processing > 0 && <a href="#/eingang" className="status"><strong>{c.processing}</strong> in Verarbeitung</a>}
          {c.inbox > 0 && <a href="#/eingang" className="status attention"><strong>{c.inbox}</strong> im Eingang</a>}
          {c.to_review > 0 && <a href="#/eingang" className="status"><strong>{c.to_review}</strong> automatisch abgelegt, ungeprüft</a>}
          {c.transactions > 0 && <a href="#/eingang" className="status"><strong>{c.transactions}</strong> Kontoumsätze zu prüfen</a>}
          {c.undated > 0 && <a href="#/eingang" className="status"><strong>{c.undated}</strong> Belege ohne Datum</a>}
          {c.error > 0 && <a href="#/eingang" className="status danger"><strong>{c.error}</strong> Nacharbeit</a>}
        </section>
      )}

      <div className="home-grid">
        <section className="panel">
          <header className="panel-head"><h2>Zuletzt hinzugefügt</h2><a href="#/archiv">Archiv</a></header>
          {data.recent.length === 0 ? (
            <Empty icon="archive" title="Noch keine Dokumente">Fotografieren Sie den ersten Beleg oder laden Sie PDFs hoch.</Empty>
          ) : (
            <div className="doc-list">{data.recent.map((d: any) => <DocRow key={d.id} doc={{ ...d, tags: [] }} />)}</div>
          )}
        </section>

        <section className="panel">
          <header className="panel-head"><h2>Ausgaben Vermietung {data.year}</h2><a href="#/ausgaben">Details</a></header>
          {exp.properties.length === 0 ? (
            <Empty icon="euro" title="Noch keine Ausgaben erfasst">Ausgaben erfassen Sie direkt am Beleg im Bereich Vermietung.</Empty>
          ) : (
            <>
              <p className="big-number">{money(exp.total)}</p>
              <p className="muted">davon {money(exp.confirmed)} bestätigt{exp.previous_year_total ? `, Vorjahr ${money(exp.previous_year_total)}` : ""}</p>
              <ul className="prop-totals">
                {exp.properties.map((p: any) => (
                  <li key={p.property}><span>{p.property}</span><strong>{money(p.total)}</strong></li>
                ))}
              </ul>
            </>
          )}
          {data.issues > 0 && <a className="notice warn" href="#/ausgaben"><Icon name="alert" size={18} /> {data.issues} Hinweis{data.issues > 1 ? "e" : ""} zu prüfen</a>}
        </section>
      </div>
    </div>
  );
}
