const DEFAULT_SERVICE_HOST = '10.98.193.46';
const SERVICE_HOST = process.env.REACT_APP_SERVICE_HOST || DEFAULT_SERVICE_HOST;
const USE_DEV_PROXY = process.env.NODE_ENV === 'development';

const parsePort = (value, fallback) => {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export const SERVICE_PORTS = Object.freeze({
  CHATBOT: parsePort(process.env.REACT_APP_CHATBOT_PORT, 5101),
  DEFECT: parsePort(process.env.REACT_APP_DEFECT_PORT, 5102),
  LITHO: parsePort(process.env.REACT_APP_LITHO_PORT, 5103),
  TCAD: parsePort(process.env.REACT_APP_TCAD_PORT, 5104),
  CIRCUIT: parsePort(process.env.REACT_APP_CIRCUIT_PORT, 5105),
  RAG_MANAGER: parsePort(process.env.REACT_APP_RAG_PORT, 5106),
  BACKEND: parsePort(process.env.REACT_APP_BACKEND_PORT, 5107),
  USER_MANAGER: parsePort(process.env.REACT_APP_USER_MANAGER_PORT, 5108),
});

const PROXY_PATHS = Object.freeze({
  [SERVICE_PORTS.CHATBOT]: '/proxy/chatbot',
  [SERVICE_PORTS.DEFECT]: '/proxy/defect',
  [SERVICE_PORTS.LITHO]: '/proxy/litho',
  [SERVICE_PORTS.TCAD]: '/proxy/tcad',
  [SERVICE_PORTS.CIRCUIT]: '/proxy/circuit',
  [SERVICE_PORTS.RAG_MANAGER]: '/proxy/rag',
  [SERVICE_PORTS.BACKEND]: '/proxy/backend',
  [SERVICE_PORTS.USER_MANAGER]: '/proxy/user-manager',
});

export const buildBaseUrl = (port) => {
  if (USE_DEV_PROXY) {
    return PROXY_PATHS[port] || `http://${SERVICE_HOST}:${port}`;
  }
  return `http://${SERVICE_HOST}:${port}`;
};

export const BACKEND_BASE_URL = buildBaseUrl(SERVICE_PORTS.BACKEND);
export const USER_MANAGER_BASE_URL = buildBaseUrl(SERVICE_PORTS.USER_MANAGER);
export const CHATBOT_BASE_URL = buildBaseUrl(SERVICE_PORTS.CHATBOT);
export const DEFECT_BASE_URL = buildBaseUrl(SERVICE_PORTS.DEFECT);
export const LITHO_BASE_URL = buildBaseUrl(SERVICE_PORTS.LITHO);
export const TCAD_BASE_URL = buildBaseUrl(SERVICE_PORTS.TCAD);
export const CIRCUIT_BASE_URL = buildBaseUrl(SERVICE_PORTS.CIRCUIT);
export const RAG_MANAGER_BASE_URL = buildBaseUrl(SERVICE_PORTS.RAG_MANAGER);

export { SERVICE_HOST };
