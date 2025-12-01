// src/setupProxy.js
const { createProxyMiddleware } = require('http-proxy-middleware');
const { parse } = require('url');

// Estos valores son los que usará el contenedor DENTRO de K8s
// (cuando hagas la imagen para K8s)
const BACKEND_HOST = process.env.BACKEND_HOST || 'http://backend:8000';
const AGENTS_PROXY = process.env.AGENTS_PROXY || 'http://agents-proxy.agents.svc.cluster.local:80';

function ensureAgentId(req, res, next) {
  const { query } = parse(req.url, true);
  const id = (query.agentID || '').toString();
  if (!id || !/^[-_a-zA-Z0-9]+$/.test(id)) {
    res.statusCode = 400;
    return res.end('Missing or invalid agentID');
  }
  req.agentId = id;
  next();
}

module.exports = function (app) {
  // --- API normal ---
  app.use(
    '/api',
    createProxyMiddleware({
      target: BACKEND_HOST,
      changeOrigin: true,
      xfwd: true,
      pathRewrite: { '^/api': '' },
      timeout: 600000,
      proxyTimeout: 600000,
    })
  );

  // --- Llamadas a agentes /agent?agentID=<uuid>&... ---
  app.use(
    '/agent',
    ensureAgentId,
    createProxyMiddleware({
      target: AGENTS_PROXY,
      changeOrigin: true,
      xfwd: true,
      timeout: 600000,
      proxyTimeout: 600000,
      // OJO: ahora enrutas por path, no por Host
      pathRewrite: (path, req) => {
        const { query } = parse(req.url, true);
        const id = req.agentId;
        const qs = new URLSearchParams(query);
        qs.delete('agentID');
        const rest = qs.toString();
        return `/agents/${id}/ask${rest ? `?${rest}` : ''}`;
      },
      logLevel: 'warn',
    })
  );
};
