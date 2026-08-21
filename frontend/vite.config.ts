import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    chunkSizeWarningLimit: 600,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "react-vendor",
              test: /node_modules[\\/](?:react|react-dom|react-router|react-router-dom|@tanstack)[\\/]/,
              priority: 40,
            },
            {
              name: "charts-vendor",
              test: /node_modules[\\/](?:recharts|lightweight-charts|d3-|victory-vendor)/,
              priority: 35,
            },
            {
              name: "editor-vendor",
              test: /node_modules[\\/](?:@codemirror|codemirror|@lezer)[\\/]/,
              priority: 30,
            },
            {
              name: "markdown-vendor",
              test: /node_modules[\\/](?:react-markdown|remark-|rehype-|unified|micromark|mdast-|hast-)/,
              priority: 25,
            },
            {
              name: "crypto-vendor",
              test: /node_modules[\\/](?:viem|@noble)[\\/]/,
              priority: 20,
            },
            {
              name: "vendor",
              test: /node_modules/,
              minSize: 20_000,
              maxSize: 350_000,
              priority: 10,
            },
          ],
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8088",
      "/ws": {
        target: "ws://localhost:8088",
        ws: true,
      },
    },
  },
});
