import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev the browser talks to Vite, which forwards /api to the back end (no CORS needed).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": process.env.VITE_PROXY_TARGET || "http://localhost:8000" } },
});
