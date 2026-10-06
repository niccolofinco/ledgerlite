# User story e piano degli incrementi

Le user story descrivono *cosa serve a chi*; la [specifica](specification.md) ne fissa le regole precise.
Formato: *Come [ruolo], voglio [funzionalità] in modo che [motivo]*.

## Ruoli

- **Contabile**: registra e consulta i movimenti.
- **Sviluppatore integratore**: scrive un client che usa l'API (anche su rete inaffidabile).
- **Revisore**: verifica che i conti siano corretti e che lo storico non sia stato alterato.
- **Operatore**: installa e gestisce il servizio.

## Backlog

| ID | User story | Regole | Endpoint | Priorità |
|---|---|---|---|:-:|
| US1 | Come contabile, voglio definire un piano dei conti in modo da classificare i movimenti. | A1, A2 | `POST/GET /accounts` | Alta |
| US2 | Come contabile, voglio registrare una scrittura in dare e avere in modo che i conti restino sempre in quadratura. | T1–T5 | `POST /transactions` | Alta |
| US3 | Come contabile, voglio consultare il saldo di un conto a una certa data in modo da sapere la situazione in quel momento. | B1 | `GET /accounts/{id}/balance` | Alta |
| US4 | Come revisore, voglio un bilancio di verifica in modo da controllare che totale dare e totale avere coincidano. | B2 | `GET /reports/trial-balance` | Alta |
| US5 | Come contabile, voglio correggere una scrittura sbagliata con uno storno in modo da non perdere lo storico. | R1–R4, T6 | `POST /transactions/{id}/reverse` | Alta |
| US6 | Come sviluppatore integratore, voglio poter ripetere una richiesta dopo un timeout in modo da non creare scritture doppie. | T7 | `POST /transactions` + `Idempotency-Key` | Media |
| US7 | Come contabile, voglio un estratto conto con saldo progressivo in modo da seguire i movimenti di un conto. | S1 | `GET /accounts/{id}/statement` | Media |
| US8 | Come contabile, voglio disattivare un conto non più usato in modo che nessuno vi registri altri movimenti. | A3, T4 | `POST /accounts/{id}/deactivate` | Media |
| US9 | Come contabile, voglio filtrare e paginare le scritture in modo da trovare rapidamente quelle che mi interessano. | — | `GET /transactions` | Bassa |
| US10 | Come operatore, voglio avviare il servizio in un container con dati persistenti in modo da installarlo ovunque. | NF1, NF4 | `GET /health` | Alta |
| US11 | Come revisore, voglio che i dati non si corrompano nemmeno con richieste parallele o errori a metà operazione. | NF2, NF3 | — | Alta |

### User story negative (vincoli assoluti)

Utili per ricavare controlli sui dati; ciascuna è tradotta in una regola positiva.

| ID | Vincolo | Diventa |
|---|---|---|
| NS1 | Nessuno deve poter registrare una scrittura sbilanciata. | T3 |
| NS2 | Nessuno deve poter modificare o cancellare una scrittura già registrata. | T6, A4 |
| NS3 | Nessuno deve poter stornare due volte la stessa scrittura. | R2 |
| NS4 | Nessuno deve poter chiudere un conto che ha ancora un saldo. | A3 |

## Incrementi (release piccole e frequenti)

Il repository segue uno sviluppo incrementale: ogni incremento aggiunge funzionalità *con i relativi test automatici*
e lascia il sistema funzionante. La cronologia dei commit riflette quest'ordine.

| Incremento | Contenuto | Storie |
|---|---|---|
| 1 | Nucleo di dominio (importi, conti, scritture) e relativi test unitari | US1, US2 (validazione) |
| 2 | Persistenza: porta `LedgerRepository`, backend in memoria e SQLite con *contract test* | US11 |
| 3 | `LedgerService`: regole di stato, storni, idempotenza, saldi, estratti, bilancio | US2–US8 |
| 4 | API REST e test di integrazione | US1–US9 |
| 5 | Test property-based sugli invarianti | NS1–NS4 |
| 6 | Container, pipeline CI/CD, documentazione | US10 |

## Pratiche agili supportate dal repository

- **Integrazione continua**: ogni push esegue lint, test (3 versioni di Python) e build del container; un test rosso blocca la pubblicazione.
- **Test automatici a più livelli** (unità, integrazione, property-based) e soglia di copertura: rendono sicuro il **refactoring**.
- **Semplicità**: dipendenze minime, nessun framework di persistenza; l'architettura a porte e adattatori permette di cambiare backend senza toccare le regole.
- **Documentazione minima ma verificata**: la tabella di tracciabilità collega ogni regola a un test, così la specifica non si scollega dal codice.
