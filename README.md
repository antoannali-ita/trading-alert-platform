# Trading Alert Platform

Piattaforma centralizzata per il monitoraggio degli alert trading.

## Ruolo

- V2: discovery
- Supabase: source of truth degli alert
- Alert worker: monitoraggio prezzo e trigger
- V3: decision engine privato
- Dashboard: controllo, audit e retry
- WhatsApp: solo segnali actionable

## Stato

Foundation in sviluppo. Modalità iniziale SHADOW. Nessun ordine automatico.

## Struttura prevista

```text
docs/
supabase/migrations/
src/
tests/
.github/workflows/
```

La specifica tecnica ufficiale è in `docs/architecture/CENTRAL_ALERT_PLATFORM_SPEC_v1.2_FINAL.md`.
