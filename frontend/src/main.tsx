import { createRoot } from "react-dom/client";
import App from "./App";
import { applyTheme, storedTheme } from "./lib";
import "./styles.css";

applyTheme(storedTheme());
createRoot(document.getElementById("root")!).render(<App />);

if ("serviceWorker" in navigator && location.protocol === "https:") {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
