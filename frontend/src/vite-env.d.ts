/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** RoutePulse backend API base URL. Defaults to http://localhost:8000/api */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
