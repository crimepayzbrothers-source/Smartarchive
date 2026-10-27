import { useState } from "react";
import { post } from "../api";

export default function Login({ setupRequired, setupPossible, onDone }: { setupRequired: boolean; setupPossible: boolean; onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [token, setToken] = useState("");
  const [totp, setTotp] = useState("");
  const [needTotp, setNeedTotp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (setupRequired && password !== password2) return setError("Die Passwörter stimmen nicht überein.");
    setBusy(true);
    try {
      if (setupRequired) await post("/api/auth/setup", { setup_token: token, username, password });
      else await post("/api/auth/login", { username, password, totp: needTotp ? totp : undefined });
      onDone();
    } catch (err: any) {
      if (err.data?.need_totp) { setNeedTotp(true); if (!err.data?.error) { setBusy(false); return; } }
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <div className="login-card">
        <div className="brand-mark" aria-hidden="true"><span /><span /><span /></div>
        <h1>SmartArchive</h1>
        <p className="login-sub">{setupRequired ? "Einmalige Einrichtung Ihres Archivs." : "Ihr Archiv für Privates und Vermietung."}</p>
        {setupRequired && !setupPossible ? (
          <p className="notice error">Einrichtung gesperrt: Bitte SETUP_TOKEN in der .env-Datei des Servers setzen und neu starten.</p>
        ) : (
          <form onSubmit={submit} className="form">
            {setupRequired && (
              <label>Einrichtungscode<input value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" required placeholder="aus der .env-Datei (SETUP_TOKEN)" /></label>
            )}
            <label>Benutzername<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required autoCapitalize="none" /></label>
            <label>Passwort<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={setupRequired ? "new-password" : "current-password"} required minLength={setupRequired ? 10 : 1} /></label>
            {setupRequired && (
              <label>Passwort wiederholen<input type="password" value={password2} onChange={(e) => setPassword2(e.target.value)} autoComplete="new-password" required minLength={10} /></label>
            )}
            {needTotp && (
              <label>Code aus der Authenticator-App<input value={totp} onChange={(e) => setTotp(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={7} autoFocus required /></label>
            )}
            {error && <p className="notice error">{error}</p>}
            <button className="primary block" disabled={busy}>{setupRequired ? "Archiv einrichten" : "Anmelden"}</button>
            {setupRequired && <p className="hint">Mindestens 10 Zeichen. Zwei-Faktor können Sie danach in den Einstellungen aktivieren.</p>}
          </form>
        )}
      </div>
    </main>
  );
}
