import { get, post } from "../api";
import { useLoad, usePoll, useToast } from "../lib";
import { SuggestionBox } from "../components/Suggestion";
import { InvoiceCard, needsInvoiceCard } from "../components/InvoiceCard";
import { TxCard } from "../components/TxCard";
import { DocRow, Empty, ErrorBox, Icon, Loading } from "../components/ui";

export default function Inbox({ onCapture }: { onCapture: () => void }) {
  const { data: both, error, loading, reload } = useLoad(() => Promise.all([get("/api/inbox"), get("/api/transactions/review")]), []);
  const data: any = both?.[0];
  const txs: any = both?.[1] || { open: [], auto: [] };
  const toast = useToast();
  const processing = data?.pending.filter((d: any) => d.status === "processing") || [];
  usePoll(processing.length > 0, reload);
  if (loading && !both) return <Loading />;
  if (error) return <ErrorBox text={error} />;

  const failed = data.pending.filter((d: any) => d.status === "error");
  const open = data.pending.filter((d: any) => d.status === "inbox");
  const auto = data.auto_filed;

  const retry = async (id: number) => {
    await post(`/api/documents/${id}/run/process`);
    toast({ kind: "ok", text: "Wird erneut verarbeitet." });
    reload();
  };

  if (!failed.length && !open.length && !auto.length && !processing.length && !txs.open.length && !txs.auto.length) {
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
                {needsInvoiceCard(d) ? <InvoiceCard doc={d} onChanged={reload} compact /> : (
                  <SuggestionBox docId={d.id} status={d.status} suggestion={d.suggestion} onChanged={reload} compact />
                )}
                {!needsInvoiceCard(d) && !d.suggestion?.labels?.category && (
                  <div className="row-extra"><a className="secondary button" href={`#/dokument/${d.id}`}>Ablage wählen</a></div>
                )}
              </DocRow>
            ))}
          </div>
        </section>
      )}

      {txs.open.length > 0 && (
        <section className="group">
          <h2 className="group-title attention">Umsätze klären <span className="count">{txs.open.length}</span></h2>
          <p className="hint">Kontoumsätze, bei denen die App nachfragt. Ihre Antworten merkt sie sich für die nächsten Monate.</p>
          <div className="tx-list">{txs.open.map((t: any) => <TxCard key={t.id} tx={t} onChanged={reload} />)}</div>
        </section>
      )}

      {txs.auto.length > 0 && (
        <section className="group">
          <h2 className="group-title">Umsätze automatisch zugeordnet <span className="count">{txs.auto.length}</span></h2>
          <p className="hint">„Passt“ bestätigt, „Rückgängig“ stellt den Umsatz zurück zur Klärung.</p>
          <div className="tx-list">{txs.auto.map((t: any) => <TxCard key={t.id} tx={t} onChanged={reload} />)}</div>
        </section>
      )}

      {auto.length > 0 && (
        <section className="group">
          <h2 className="group-title">Automatisch abgelegt <span className="count">{auto.length}</span></h2>
          <p className="hint">Kurz prüfen: „Passt“ bestätigt die Ablage, „Rückgängig“ holt das Dokument zurück in den Eingang.</p>
          <div className="doc-list">
            {auto.map((d: any) => (
              <DocRow key={d.id} doc={d}>
                {needsInvoiceCard(d) ? <InvoiceCard doc={d} onChanged={reload} compact /> : (
                  <SuggestionBox docId={d.id} status={d.status} suggestion={d.suggestion} onChanged={reload} compact />
                )}
              </DocRow>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
