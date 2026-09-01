# Trading Alert Platform — Session Refresh & Worker Roadmap V1

**Status:** FROZEN FOR IMPLEMENTATION  
**Repository:** `antoannali-ita/trading-alert-platform`  
**Scope:** evoluzione del worker Supabase-centrico già validato in SHADOW, senza creare un secondo sistema parallelo.

## 1. Obiettivo

Evolvere il worker attuale affinché:

1. ogni sessione Italia e USA parta da una fotografia fresca del mercato;
2. il refresh iniziale non dipenda dal `next_check_at` ereditato dal giorno precedente;
3. siano riconosciuti gap overnight, attraversamenti del trigger e superamenti del `Max Buy`;
4. siano rispettati rate limit e budget crediti del provider anche durante il refresh iniziale;
5. un solo worker possa reclamare ed eseguire il refresh di sessione;
6. Twelve Data resti provider PRIMARY;
7. Yahoo sia FALLBACK operativo in modalità `DEGRADED`;
8. TradingView resti solo strumento di validazione/controllo, non backend automatico operativo;
9. dopo il refresh il sistema torni al polling adattivo 5/15/30 minuti;
10. Supabase resti l’unica source of truth.

## 2. Architettura unica

Non vengono creati due binari applicativi.

```text
V2 / Manual / Site
        ↓
     Supabase
        ↓
market_session_state
        ↓
   Alert Worker
        ↓
 ProviderManager
        ↓
Twelve Data / Yahoo fallback
        ↓
 Trigger Engine
        ↓
     V3 Private
        ↓
Notification Policy
```

Il vecchio polling potrà convivere solo durante la fase di validazione. Dopo il go-live del nuovo worker verrà disattivato. Supabase resta la sola source of truth.

## 3. Scheduler

GitHub Actions avvia il worker ogni 5 minuti.

```yaml
schedule:
  - cron: "*/5 * * * *"
```

Il cron non implica che tutti i ticker vengano interrogati ogni 5 minuti. Il worker decide se eseguire:

- `SESSION_REFRESH`, oppure
- `ADAPTIVE_CHECK` basato su `next_check_at`.

## 4. Market calendar

Italia e USA sono gestiti separatamente con calendario e timezone propri.

```text
ITALIA → Europe/Rome / calendario XMIL
USA    → America/New_York / calendario NYSE-NASDAQ
```

Devono essere gestiti:

- festività;
- early close;
- DST;
- sessione regolare;
- nessun trigger operativo standard in pre-market/post-market, salvo futura modifica esplicita.

## 5. SESSION_REFRESH_V1

### 5.1 Start

Il refresh diventa dovuto dopo 3 minuti dall’apertura ufficiale della sessione.

```text
Italia: apertura 09:00 → refresh_due_at 09:03 Europe/Rome
USA:    apertura 09:30 → refresh_due_at 09:33 America/New_York
```

Il primo worker disponibile dopo `refresh_due_at` prova a reclamare il refresh.

Non è richiesto che GitHub Actions parta esattamente al minuto +3. Se il runner parte più tardi, il refresh viene recuperato finché non risulta completato.

### 5.2 Scope

Il `SESSION_REFRESH`:

- ignora temporaneamente il vecchio `next_check_at`;
- considera tutti gli alert `ACTIVE` del mercato;
- deduplica i ticker;
- aggiorna dati di mercato;
- ricalcola distanza da trigger / entry / max buy;
- rileva gap;
- assegna il nuovo `next_check_at`;
- torna poi alla normale logica adattiva.

## 6. market_session_state

Serve una tabella/stato persistente almeno con:

```text
id
market
session_date
opened_at
refresh_due_at
status
claimed_by
claimed_at
claim_expires_at
heartbeat_at
refresh_started_at
refresh_completed_at
result
created_at
updated_at
```

### 6.1 Stati sessione

```text
PENDING
CLAIMED
COMPLETED
COMPLETED_WITH_PENDING
FAILED
```

### 6.2 Claim atomico con lease

Il refresh non può essere deciso leggendo un semplice flag. Deve usare un claim atomico con lease, stesso principio già validato per gli alert.

Esempio concettuale:

```sql
UPDATE alert_platform.market_session_state
SET status = 'CLAIMED',
    claimed_by = :worker_id,
    claimed_at = now(),
    claim_expires_at = now() + interval '180 seconds'
WHERE id = :session_id
  AND (
        status = 'PENDING'
        OR (
          status = 'CLAIMED'
          AND claim_expires_at < now()
        )
      )
RETURNING *;
```

Un solo worker conquista il refresh. Se il worker muore, la lease scade e un altro worker può recuperarlo.

Lease iniziale coerente con la spec esistente:

```text
claim_lease_seconds = 180
heartbeat_threshold_seconds = 60
```

## 7. SESSION_REFRESH_MAX_DURATION

Il refresh non deve restare aperto indefinitamente.

Configurazione iniziale:

```text
session_refresh_max_duration_minutes = 15
```

Se il refresh supera la durata massima:

- i ticker già processati conservano il loro risultato;
- i ticker non ancora processati diventano `PENDING_REFRESH`;
- la sessione passa a `COMPLETED_WITH_PENDING`;
- i `PENDING_REFRESH` vengono ripresi con priorità alta dal ciclo normale.

## 8. Stato per ticker durante il refresh

### 8.1 Stati transitori

```text
PENDING_REFRESH
PENDING_OPEN
PROCESSING
```

### 8.2 Stati terminali

```text
UPDATED
DEGRADED
NO_OPEN_DATA
FAILED
```

`PENDING_OPEN` non è terminale.

Flusso iniziale:

```text
PENDING_OPEN
→ retry +2 min
→ retry +5 min
→ ancora nessun open valido
→ NO_OPEN_DATA
```

Questo copre titoli sospesi, illiquidi o senza un vero trade di apertura disponibile.

## 9. Rate limiting e batching

Il rate limiter è sempre attivo, anche durante il `SESSION_REFRESH`.

Il refresh non può inviare tutti i ticker contemporaneamente solo perché è un evento di apertura.

Il worker deve:

1. deduplicare ticker;
2. ordinare la coda per priorità operativa;
3. creare batch compatibili con il provider;
4. rispettare `market_data_max_requests_per_minute`;
5. rispettare `market_data_max_symbols_per_batch`;
6. spostare in avanti i ticker che non possono essere processati nel minuto corrente;
7. non bloccare indefinitamente la sessione.

## 10. Priorità della coda di session refresh

La priorità è risk-based, non FIFO puro.

Ordine iniziale:

```text
1. gap / overnight-risk candidates
2. ticker <2% dal trigger
3. ticker 2-5% dal trigger
4. PRE_BUY_HIGH / IN_BUY_ZONE / Max Buy vicini, se questi metadati sono disponibili
5. altri ACTIVE
6. ticker lontani / bassa priorità
```

Principio:

```text
RISK PRIORITY > SESSION COMPLETENESS
```

Non si sacrifica un ticker operativo vicino all’entry per aggiornare prima un ticker lontano solo per rendere il refresh formalmente completo.

## 11. ProviderManager

Il worker non deve contenere logiche sparse del tipo `if Twelve fails then Yahoo`.

Viene introdotto un `ProviderManager`.

```text
MarketDataProvider
├── TwelveDataProvider      PRIMARY
├── YahooProvider           FALLBACK
└── TradingViewVerifier     VALIDATION / HUMAN CONTROL
```

### 11.1 Twelve Data

Resta la sorgente automatica PRIMARY.

Deve fornire almeno:

```text
ticker
current_price
timestamp
market_status
provider
```

Per `SESSION_REFRESH` verrà esteso, quando disponibile, a:

```text
open
previous_close
high
low
volume
```

### 11.2 Yahoo

Yahoo viene implementato come FALLBACK operativo.

Si attiva in caso di:

```text
PRIMARY_TIMEOUT
PRIMARY_RATE_LIMIT
PRIMARY_QUOTA_EXHAUSTED
PROVIDER_ERROR
DATA_STALE
INVALID_RESPONSE
```

I dati Yahoo vengono marcati come `DEGRADED` / `FALLBACK_OK` e non vengono confusi con il dato primary.

### 11.3 TradingView

TradingView resta escluso dal backend automatico operativo.

Uso consentito nel progetto:

- controllo umano;
- validazione visiva;
- confronto in caso di anomalia;
- eventuali tool manuali futuri.

Non diventa source of truth né provider primario/fallback del worker automatico.

## 12. Data quality

Ogni osservazione di mercato deve registrare almeno:

```text
ticker
price
timestamp
provider
market_status
data_quality
```

Stati minimi:

```text
PRIMARY_OK
PRIMARY_TIMEOUT
PRIMARY_RATE_LIMIT
PRIMARY_QUOTA_EXHAUSTED
FALLBACK_OK
FALLBACK_MISMATCH
DATA_STALE
PENDING_OPEN
NO_OPEN_DATA
ALL_PROVIDERS_FAILED
```

Il provider effettivamente usato deve essere sempre auditabile.

## 13. Credit Budget Manager

Il consumo Twelve Data diventa un vincolo applicativo.

Config iniziali da supportare:

```text
twelve_max_requests_per_minute
twelve_daily_budget
twelve_warning_threshold
fallback_enabled
```

Il worker deve rispettare il budget sia nel ciclo normale sia nel refresh iniziale.

Classi di priorità:

```text
CRITICAL → <2% dal trigger, gap candidates, entry/max buy vicini
HIGH     → 2-5%
NORMAL   → >5%
LOW      → lontani / poco rilevanti
```

Comportamento:

```text
crediti sufficienti
→ Twelve Data

budget sotto warning
→ priorità CRITICAL/HIGH su Twelve
→ NORMAL/LOW rinviabili o fallback

quota esaurita / rate limit
→ Yahoo fallback DEGRADED
```

Un fallimento o esaurimento Twelve Data non deve far saltare l’intero `SESSION_REFRESH`.

## 14. Gap detection

Il gap viene calcolato solo quando i dati necessari sono validi.

Dati richiesti quando disponibili:

```text
previous_close
open
current_price
trigger
entry_range
max_buy
```

### 14.1 Soglie configurabili

Non hard-code nel worker.

Valori iniziali proposti:

```text
gap_minor_pct    = 0.01
gap_material_pct = 0.02
gap_large_pct    = 0.03
gap_extreme_pct  = 0.05
```

### 14.2 Classificazioni indipendenti

Le categorie possono convivere.

```text
NORMAL_OPEN
GAP_TOWARD_TRIGGER
GAP_THROUGH_TRIGGER
GAP_IN_BUY_ZONE
GAP_ABOVE_MAX_BUY
GAP_AWAY_FROM_TRIGGER
GAP_MINOR
GAP_MATERIAL
GAP_LARGE
GAP_EXTREME
```

Esempio:

```text
GAP_LARGE
+
GAP_THROUGH_TRIGGER
+
IN_BUY_ZONE
```

oppure:

```text
GAP_EXTREME
+
GAP_ABOVE_MAX_BUY
→ NO CHASE
```

Regola obbligatoria:

```text
Price > Max Buy
→ NON INSEGUIRE
```

`GAP_THROUGH_TRIGGER` è una relazione rispetto al trigger, non una semplice soglia percentuale.

## 15. Polling adattivo dopo il refresh

Dopo `SESSION_REFRESH`, il worker torna alla logica ordinaria già definita.

```text
distance <2%    → next_check_at +5 min
distance 2-5%   → next_check_at +15 min
distance >5%    → next_check_at +30 min
```

Le soglie devono essere lette da `system_config`.

## 16. alert_runs e retry provider

Prima dell’attivazione automatica completa deve essere persistita la storia dei tentativi.

Serve per distinguere:

```text
1° errore provider → retry +1 min
2° errore          → retry +5 min
errori successivi  → fallback / scheduling definito dalla policy
```

Gli errori devono essere auditabili con i codici già previsti dalla piattaforma:

```text
CHECK_OK
DATA_TIMEOUT
DATA_RATE_LIMIT
DATA_STALE
PROVIDER_ERROR
V3_ERROR
NOTIFICATION_ERROR
LOCK_ERROR
```

## 17. Concorrenza

Restano obbligatori i principi già adottati:

- claim atomico;
- `FOR UPDATE SKIP LOCKED` per alert quando applicabile;
- ownership check prima di modifiche/release;
- lease con scadenza;
- heartbeat;
- recovery di claim scaduti;
- nessun doppio `SESSION_REFRESH` della stessa sessione.

## 18. Modalità SHADOW

Durante implementazione e collaudo:

```text
enable_market_data = true
enable_auto_trigger = false
enable_v3 = false
send_whatsapp = false
```

Il worker può:

- leggere alert;
- usare provider reali;
- aggiornare scheduling;
- registrare data quality;
- eseguire session refresh;
- esercitare failover.

Non può ancora:

- generare BUY automatici;
- invocare V3 automaticamente;
- inviare WhatsApp operativi.

## 19. Ordine di implementazione congelato

```text
1. alert_runs + persistence errori/retry
2. market_session_state
3. claim/lease atomico SESSION_REFRESH
4. market calendar Italia/USA
5. provider rate limiter + credit budget
6. session refresh queue prioritizzata
7. PENDING_OPEN / retry / NO_OPEN_DATA
8. estensione Twelve Data a quote complete utili al refresh
9. gap detection + soglie system_config
10. ProviderManager
11. Yahoo fallback
12. cron GitHub ogni 5 minuti
13. SHADOW automatico per più sessioni reali
14. validazione dati Italia/USA e metriche health
15. Trigger Engine
16. V3 integration
17. WhatsApp / Notification Policy
18. disattivazione vecchio polling automatico
```

Nessuna fase successiva deve anticipare i gate di sicurezza precedenti.

## 20. Test obbligatori prima del go-live

### Database / concorrenza

- due worker tentano il claim della stessa sessione → uno solo vince;
- lease scaduta → recovery corretto;
- refresh completato → non reclamabile di nuovo;
- timeout refresh → `COMPLETED_WITH_PENDING`;
- ticker residui → `PENDING_REFRESH` ripresi dal ciclo normale.

### Market calendar

- Italia giorno normale;
- USA giorno normale;
- festività;
- early close;
- settimane di disallineamento DST Europa/USA.

### Rate limit / credits

- batch oltre limite;
- 8 richieste/minuto simulate;
- quota quasi esaurita;
- quota esaurita;
- fallback automatico;
- nessun ticker CRITICAL perso per priorità FIFO errata.

### Session refresh

- nessun gap;
- gap verso trigger;
- gap through trigger;
- gap dentro buy zone;
- gap oltre Max Buy → NO CHASE;
- titolo senza open → `PENDING_OPEN` → `NO_OPEN_DATA`;
- refresh >15 minuti → completion controllata.

### Provider failover

- Twelve timeout;
- rate limit;
- quota exhausted;
- risposta stale/invalid;
- Yahoo fallback OK;
- tutti i provider falliscono → `ALL_PROVIDERS_FAILED` e nessun BUY automatico.

## 21. Gate per attivazione cron automatico

Prima di attivare il worker schedulato ogni 5 minuti devono essere verdi almeno:

```text
DB migrations / RPC
Python CI
Twelve Data smoke
Supabase live smoke
SESSION_REFRESH concurrency tests
market calendar tests
rate limit / budget tests
fallback tests
SHADOW E2E
```

Il cron viene attivato inizialmente ancora in SHADOW.

## 22. Decisione architetturale finale

La piattaforma procederà così:

```text
APERTURA MERCATO
        ↓
     +3 minuti
        ↓
SESSION_REFRESH dovuto
        ↓
claim atomico + lease
        ↓
coda risk-based + rate limit
        ↓
Twelve Data PRIMARY
        ↓ fallisce
Yahoo FALLBACK / DEGRADED
        ↓
gap + trigger distance + Max Buy
        ↓
refresh completato o timeout controllato
        ↓
next_check_at 5 / 15 / 30
        ↓
ADAPTIVE_CHECK durante la sessione
```

Obiettivo operativo: **ogni sessione Italia e USA riparte da dati freschi, senza perdere gap overnight, senza inseguire prezzi oltre Max Buy, senza duplicare refresh e senza consumare i crediti market-data in modo incontrollato.**
