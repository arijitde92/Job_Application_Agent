# Job Application Agent — Frontend

React + Vite (JavaScript) single-page app. Authenticates against the FastAPI
backend, lets users link a GitHub profile and upload a resume, kicks off the
CrewAI tailoring pipeline, and streams live progress via Server-Sent Events.

## Layout

```
frontend/
├── src/
│   ├── assets/        # Static media (images, icons)
│   ├── components/    # Reusable UI components
│   ├── pages/         # Route-level views
│   ├── context/       # React context providers (AuthContext)
│   ├── hooks/         # Custom React hooks
│   ├── services/      # API client (api.js — axios instance + interceptors)
│   ├── App.jsx        # Route layout
│   └── main.jsx       # Entry point
├── public/
├── .env.example
├── package.json
└── vite.config.js     # Dev server proxies /api → http://localhost:8000
```

## Setup

```bash
cp .env.example .env
npm install
```

## Develop

```bash
npm run dev        # http://localhost:5173
```

The dev server proxies `/api/*` to the backend at `http://localhost:8000`
(see `vite.config.js`), so run the backend alongside it.

## Build

```bash
npm run build      # outputs to dist/
npm run preview
```
