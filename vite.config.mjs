import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({command,mode}) => {
 const previewDemo=command==="serve" && loadEnv(mode,process.cwd(),"").PREVIEW_MODE==="demo";
 return {
  build: {
    outDir: "dist/client",
    rollupOptions: {
      output: {
        manualChunks: {
          icons: ["@phosphor-icons/react"],
          react: ["react", "react-dom"],
        },
      },
    },
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: ["terminal.local"],
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
  },
  plugins: [react(), ...(previewDemo?[{name:"explicit-preview-demo",configureServer(server){server.middlewares.use("/api",(_req,res)=>{res.statusCode=503;res.setHeader("Content-Type","application/json");res.end(JSON.stringify({mode:"sites-demo",detail:"Explicit local UI preview: demo data only"}));});}}]:[])],
};
});
