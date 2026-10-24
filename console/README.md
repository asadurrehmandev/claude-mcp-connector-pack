# Switchboard console

Next.js console for the Switchboard API. Setup, ports, and the security model are in the repository README.

```bash
npm install
npm run dev
```

The dev server listens on http://127.0.0.1:4318 and calls http://127.0.0.1:4317/console unless `NEXT_PUBLIC_API_BASE` is set.
