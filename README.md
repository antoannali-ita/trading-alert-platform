# Trading Alert Platform

Piattaforma centralizzata per il monitoraggio degli alert trading.

## Ruolo

- V2: discovery
- Supabase: source of truth degli alert
- Alert worker: monitoraggio prezzo, trigger e invio notifiche (SHADOW + LIVE)
- V3: decision engine privato
- Dashboard: controllo, audit e retry
- WhatsApp: canale di notifica attivo in produzione per segnali actionable

## Stato

L'alert worker è in produzione: i cicli SHADOW (session refresh + polling adattivo,
solo osservazione) e LIVE (valutazione trigger e invio notifiche reali) sono
entrambi schedulati ogni 5 minuti su GitHub Actions. Le notifiche di produzione
vengono inviate via WhatsApp (CallMeBot); Telegram ed e-mail sono canali in
collaudo (vedi workflow `Multichannel Delivery Test`).

Nessun ordine automatico: `enable_auto_trigger` e `enable_v3` restano disattivati
by design (guardia esplicita nel worker SHADOW). V2 (discovery) e V3 (decision
engine) non fanno ancora parte di questo repository.

## Struttura prevista

```text
docs/
supabase/migrations/
src/
tests/
.github/workflows/
```

La roadmap tecnica di session refresh e worker è in
`docs/SESSION_REFRESH_AND_WORKER_ROADMAP_V1.md`.
