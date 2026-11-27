# kalliope-pbx-client

Client Python **non ufficiale** per le API REST di [KalliopePBX](https://www.kalliope.com/) V4: autenticazione, registro chiamate (CDR), interni, code, gruppi di chiamata e controlli orari. Un solo file, solo libreria standard.

*Unofficial Python client for the KalliopePBX V4 REST API (auth header, CDR, extensions, queues, call groups, time checks). Single file, standard library only. Docs are in Italian.*

> Progetto indipendente, non affiliato a NetResults S.r.l. «Kalliope» e «KalliopePBX» sono marchi dei rispettivi titolari.

## Perché

La documentazione ufficiale spiega l'autenticazione e il registro chiamate, ma il resto va ricavato dallo Swagger dell'istanza e dalle prove. Qui c'è quello che serve per partire, più le trappole incontrate lavorando su un'istanza multi-tenant in cloud.

## Uso

```bash
export KALLIOPE_URL=https://pbx.example.com      # base dell'API
export KALLIOPE_DOMAIN=default                    # dominio del tenant
export KALLIOPE_USER=utente
export KALLIOPE_PASSWORD='...'

python kalliope_client.py cdr --day 2026-11-27 --inbound-only   # chiamate entrate, con esito
python kalliope_client.py queues
python kalliope_client.py get /api/doc.json > swagger.json      # elenco completo degli endpoint
```

```python
import datetime as dt
from kalliope_client import KalliopeClient, classify_call

k = KalliopeClient.from_env()
for call in k.cdr(dt.date.today()):
    if call["source_type"] == "ibl":                 # entrata da una linea esterna
        print(call["start_datetime"], call["called"], classify_call(call))
```

## Autenticazione

Nessuna sessione: ogni richiesta porta un header monouso calcolato dal client.

1. `GET /rest/salt/<dominio>` (anonima, con `Accept: application/json`) restituisce il salt del tenant.
2. `digestPassword = sha256_hex(password + "{" + salt + "}")`
3. `Digest = base64(sha256_binario(nonce + digestPassword + username + dominio + created))`
4. `X-authenticate: RestApiUsernameToken Username="…", Domain="…", Digest="…", Nonce="…", Created="…"`

`Nonce` è esadecimale, sempre nuovo; `Created` è l'ora UTC (`YYYY-MM-DDThh:mm:ssZ`) e non può differire di più di 5 minuti dall'ora del centralino. I test verificano la formula con il vettore di prova del manuale ufficiale.

## Cosa copre

| Area | Metodi | Endpoint |
|---|---|---|
| Registro chiamate | `cdr()`, `cdr_search()` | `/rest/cdr/{summary\|detailed}/…` |
| Interni | `extensions()` | `/rest/extension` |
| Code | `queues()`, `queue()`, `queue_members()`, `queue_add_member()`, `queue_remove_member()` | `/api/pbx/v2/queue`, `/api/pbx/v2/queueAccount` |
| Gruppi di chiamata | `callgroups()`, `callgroup_priorities()` | `/api/pbx/v2/callgroup` |
| Controlli orari | `checktimes()`, `checktime()`, `set_checktime_periods()` | `/rest/checktime` |
| Tutto il resto | `get()`, `request()` | qualsiasi percorso |

Funzioni di aiuto: `classify_call()` (risposta, segreteria, solo annuncio, persa e dove), `writable_period()` e `writable_destination()` (dal formato di lettura a quello di scrittura), `mask_number()`.

## Cose che la documentazione non dice

Osservate su un'istanza 4.x multi-tenant; su altre versioni possono cambiare.

- **Il CDR non compare nello Swagger** (`/api/doc.json`), ma `/rest/cdr/...` funziona.
- **Formato `detailed`**: ogni chiamata ha `detail`, la lista delle tratte (`dest_type`: `ivr`, `checktime`, `queue`, `callg`, `voicemail`, `obl`), e ogni tratta ha `userEvents` con i dispositivi che hanno squillato (`userExten`, `userAccount`, `answerTime`, `ringTimeSeconds`, `exitCause`).
- **Numeri mascherati**: senza il permesso di privacy completo le ultime tre cifre dei numeri esterni arrivano come `xxx`.
- **503 «Lock can not be acquired»**: le scritture falliscono finché qualcuno tiene aperto il pannello di amministrazione. Il client riprova.
- **`PATCH` solo con `Content-Type: application/json`**: con `merge-patch+json` risponde 400.
- **Controlli orari**: il `PATCH` di `checktimePeriods` *sostituisce* tutti i periodi; in lettura le ore sono `hourFrom`/`minuteFrom`, in scrittura `timeFrom`/`timeTo` («HH:MM»), senza `id`.
- **Destinazioni** (`failover`): `destinationType` 4 interno, 5 gruppo, 7 coda, 13 chiusura, 15 menu vocale, 17 casella vocale, 18 numero esterno. In scrittura senza `id`.
- **Interni**: l'elenco contiene campi password (`extensions()` li toglie). `PUT` rifiuta i campi di sola lettura come «extra fields»; `PATCH` risponde 405.
- **Code**: i membri sono singoli account (dispositivi), non interni. Tipi di account: 2 telefono IP, 9 KPE Desktop, 10 KPE Mobile, 11 WebCTI.
- **Liste**: la v2 risponde `{"items": [...]}`, la vecchia API a volte una lista semplice. Paginazione: `?itemsPerPage=200`.

## Avvertenze

- Le scritture cambiano un centralino in produzione: leggere, salvare lo stato, scrivere, rileggere.
- I numeri dei chiamanti sono dati personali: la riga di comando li maschera salvo `--show-numbers`.
- Stato: letture e scritture ricavate da script usati in produzione; **questo pacchetto, così riordinato, è coperto solo dai test senza rete**.

## Test

```bash
python -m unittest test_kalliope_client
```

## Documentazione ufficiale

- [API REST – wiki Kalliope](https://wiki.kalliope.com/it/latest/API%20Rest.html)
- Swagger della propria istanza: `/api/doc` e `/api/doc.json`
- [phpRestApiUtils](https://github.com/NetResultsIT/phpRestApiUtils), esempio PHP del produttore

## Licenza

MIT.
