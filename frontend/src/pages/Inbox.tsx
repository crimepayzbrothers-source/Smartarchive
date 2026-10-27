import { get, post } from "../api";
import { useLoad, usePoll, useToast } from "../lib";
import { SuggestionBox } from "../components/Suggestion";
import { DocRow, Empty, ErrorBox, Icon, Loading } from "../components/ui";

export default function Inbox({ onCapture }: { onCapture: () => void }) {
  const { data, error, loading, reload } = useLoad(() => get("/api/inbox"), []);
  const toast = useToast();
  const processing = data?.pending.filter((d: any) => d.status === "processing") || [];
  usePoll(processing.length > 0, reload);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox text={error} />;

  const failed = data.pending.filter((d: any) => d.status === "error");
  const open = data.pending.filter((d: any) => d.status === "inbox");
  const auto = data.auto_filed;

  const retry = async (id: number) => {
    await post(`/api/documents/${id}/run/process`);
    toast({ kind: "ok", text: "Wird erneut verarbeitet." });
    reload();
  };

  if (!failed.length && !open.length && !auto.length && !processing.length) {
    return (
      <div className="page">
        <h1 className="page-title">Eingang</h1>
        <Empty icon="inbox" title="Eingang ist leer">
          Neue Belege landen hier, wenn die App sich bei der Ablage nicht sicher ist.
          <button className="primary" onClick={onCapture}><Icon name="camera" size={20} /> Beleg erfassen</button>
        </Empty>
      </div>
    );
  }

  return (
    <div className="page inbox">
      <h1 className="page-title">Eingang</h1>

      {failed.length > 0 && (
        <section className="group">
          <h2 className="group-title danger">Nacharbeit</h2>
          <div className="doc-list">
            {failed.map((d: any) => (
              <DocRow key={d.id} doc={d}>
                <div className="row-extra">
                  <p className="notice error small">{d.error || "Verarbeitung fehlgeschlagen."}</p>
                  <button className="secondary" onClick={() => retry(d.id)}>Erneut versuchen</button>
                </div>
              </DocRow>
            ))}
          </div>
        </section>
      )}

      {processing.length > 0 && (
        <section className="group">
          <h2 className="group-title">Wird verarbeitet</h2>
          <p className="hint">Texterkennung und Einordnung laufen. Die Liste aktualisiert sich selbst.</p>
          <div className="doc-list">{processing.map((d: any) => <DocRow key={d.id} doc={d} />)}</div>
        </section>
      )}

      {open.length > 0 && (
        <section className="group">
          <h2 className="group-title attention">Bitte ablegen <span className="count">{open.length}</span></h2>
          <div className="doc-list">
            {open.map((d: any) => (
              <DocRow key={d.id} doc={d}>
                <SuggestionBox docId={d.id} status={d.status} suggestion={d.suggestion} onChanged={reload} compact />
                {!d.suggestion?.labels?.category && (
                  <div className="row-extra"><a className="secondary button" href={`#/dokument/${d.id}`}>Ablage wählen</a></div>
                )}
              </DocRow>
            ))}
          </div>
        </section>
      )}

      {auto.length > 0 && (
        <section className="group">
          <h2 className="group-title">Automatisch abgelegt <span className="count">{auto.length}</span></h2>
          <p className="hint">Kurz prüfen: „Passt“ bestätigt die Ablage, „Rückgängig“ holt das Dokument zurück in den Eingang.</p>
          <div className="doc-list">
            {auto.map((d: any) => (
              <DocRow key={d.id} doc={d}>
                <SuggestionBox docId={d.id} status={d.status} suggestion={d.suggestion} onChanged={reload} compact />
              </DocRow>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
