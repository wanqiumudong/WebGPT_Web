const { createProxyMiddleware } = require('http-proxy-middleware');

const PROXY_HOST =
  process.env.REACT_APP_PROXY_TARGET_HOST ||
  process.env.REACT_APP_SERVICE_HOST ||
  '10.98.193.46';

const servicePorts = {
  backend: process.env.REACT_APP_BACKEND_PORT || 5107,
  'user-manager': process.env.REACT_APP_USER_MANAGER_PORT || 5108,
  chatbot: process.env.REACT_APP_CHATBOT_PORT || 5101,
  defect: process.env.REACT_APP_DEFECT_PORT || 5102,
  litho: process.env.REACT_APP_LITHO_PORT || 5103,
  tcad: process.env.REACT_APP_TCAD_PORT || 5104,
  circuit: process.env.REACT_APP_CIRCUIT_PORT || 5105,
  rag: process.env.REACT_APP_RAG_PORT || 5106,
};

const createServiceProxy = (serviceName, port) =>
  createProxyMiddleware({
    target: `http://${PROXY_HOST}:${port}`,
    changeOrigin: true,
    ws: false,
    secure: false,
    proxyTimeout: 300000,
    timeout: 300000,
    pathRewrite: {
      [`^/proxy/${serviceName}`]: '',
    },
    onError(err, req, res) {
      if (res.headersSent) {
        return;
      }
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          message: `Proxy error for ${serviceName}`,
          error: err.message,
        }),
      );
    },
  });

module.exports = function setupProxy(app) {
  Object.entries(servicePorts).forEach(([serviceName, port]) => {
    app.use(`/proxy/${serviceName}`, createServiceProxy(serviceName, port));
  });
};
