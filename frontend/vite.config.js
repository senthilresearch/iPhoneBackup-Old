import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Backend runs on 8765 (port 8000 is commonly taken by other local apps).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
});
