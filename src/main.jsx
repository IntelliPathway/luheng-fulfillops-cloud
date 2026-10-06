import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";
import "./theme.css";
import {initializePublicConfig} from "./runtime-config.js";

async function boot(){
try {
await initializePublicConfig();
createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
} catch {
 const notice=document.createElement('p');
 notice.setAttribute('role','alert');
 notice.textContent='站点接入配置异常，业务操作已暂停。请联系管理员。';
 document.getElementById('root').replaceChildren(notice);
}

}
boot();
