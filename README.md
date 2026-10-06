# LedgerLite

Servizio di **contabilità a partita doppia** con API REST, scritto in Python (Flask + SQLite).
Registra scritture sempre bilanciate, in modo idempotente; gli errori si correggono con **storni** (il libro mastro è
append-only); espone saldi, estratti conto e bilancio di verifica.

> Progetto per la prova progettuale dell'esame. Specifica: [`docs/specification.md`](docs/specification.md) ·
> User story: [`docs/user-stories.md`](docs/user-stories.md) ·
> Architettura e CI/CD: [`docs/architecture.md`](docs/architecture.md) · Test: [`docs/testing.md`](docs/testing.md) ·
> Contratto API: [`docs/openapi.yaml`](docs/openapi.yaml)

## Avvio rapido

### Con Docker

```bash
docker compose up --build        # API su http://localhost:8000, dati nel volume "ledger-data"
curl http://localhost:8000/health
```

### In locale

```bash
python -m venv .venv && source .venv/bin/activate
make install                     # dipendenze + pacchetto in modalità editable
make run                         # server di sviluppo su http://localhost:5000 (DB: ledgerlite.db)
```

## Esempio d'uso

```bash
API=http://localhost:8000
post() { curl -s -X POST "$API$1" -H 'Content-Type: application/json' "${@:2}"; }

CASH=$(post /accounts  -d '{"code":"CASH","name":"Cassa","type":"asset"}'  | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')
SALES=$(post /accounts -d '{"code":"SALES","name":"Vendite","type":"income"}' | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# una vendita da 100,50 €; l'header rende la richiesta ripetibile senza duplicati
post /transactions -H 'Idempotency-Key: fattura-1' -d "{
  \"date\": \"2026-10-01\", \"description\": \"Fattura 1\",
  \"postings\": [
    {\"account_id\": \"$CASH\",  \"side\": \"debit\",  \"amount\": \"100.50\"},
    {\"account_id\": \"$SALES\", \"side\": \"credit\", \"amount\": \"100.50\"}]}"

curl -s $API/accounts/$CASH/balance        # {"balance": "100.50", "normal_side": "debit", ...}
curl -s $API/reports/trial-balance         # {"total_debit": "100.50", "total_credit": "100.50", "balanced": true, ...}
```

| Metodo e percorso | Descrizione |
|---|---|
| `POST /accounts`, `GET /accounts`, `GET /accounts/{id}` | Piano dei conti |
| `POST /accounts/{id}/deactivate` | Disattiva un conto (solo con saldo zero) |
| `GET /accounts/{id}/balance?as_of=` | Saldo alla data |
| `GET /accounts/{id}/statement?date_from=&date_to=` | Estratto conto con saldo progressivo |
| `POST /transactions` (+ `Idempotency-Key`) | Registra una scrittura bilanciata |
| `GET /transactions`, `GET /transactions/{id}` | Elenco (filtri, paginazione) e dettaglio |
| `POST /transactions/{id}/reverse` | Storna una scrittura |
| `GET /reports/trial-balance?as_of=` | Bilancio di verifica |
| `GET /health` | Health check |

Errori: `422` validazione · `404` non trovato · `409` conflitto, con corpo `{"error": {"code", "message"}}`.

## Struttura del repository

```
ledgerlite/
├── src/ledgerlite/
│   ├── domain/            # entità, Money, errori (nessun I/O)
│   ├── repositories/      # porta LedgerRepository + backend in memoria e SQLite
│   ├── services.py        # LedgerService: regole di business e report
│   └── api/               # Flask: rotte, serializer, gestione errori
├── tests/
│   ├── unit/              # dominio, servizio, serializer
│   ├── integration/       # contract test, SQLite reale, API, end-to-end
│   └── property/          # test property-based (Hypothesis)
├── docs/                  # specifica, architettura, strategia di test, OpenAPI
├── scripts/smoke_test.sh  # test end-to-end su un'istanza in esecuzione
├── .github/workflows/     # pipeline CI/CD
├── Dockerfile · docker-compose.yml · Makefile · pyproject.toml
```

## Test e copertura

```bash
make test               # tutta la suite
make test-unit          # solo unitari
make test-integration   # solo integrazione
make test-property      # solo property-based
make cov                # copertura (linee + rami), fallisce sotto il 90 %; report in htmlcov/
make lint               # ruff
```

## Come il progetto soddisfa i requisiti della prova

| Requisito | Punti | Dove |
|---|:-:|---|
| Complessità del progetto | 3 | Dominio contabile con invarianti (partita doppia, storni, idempotenza), due backend di persistenza con contratto condiviso, API REST, concorrenza multi-processo, report. Architettura a strati in [`docs/architecture.md`](docs/architecture.md). |
| Repository Git strutturato | 2 | Cartelle per responsabilità (`src/`, `tests/{unit,integration,property}`, `docs/`, `scripts/`, `.github/`); *Conventional Commits* (sotto); `.gitignore`, tag `v0.1.0`. |
| Specifica almeno parziale | 3 | [`docs/specification.md`](docs/specification.md): modello di dominio, invarianti formali (INV1–INV7), regole numerate (A/T/R/B/S), pre/post-condizioni delle operazioni, tracciabilità regole → test; [`docs/user-stories.md`](docs/user-stories.md): user story e backlog; contratto [`docs/openapi.yaml`](docs/openapi.yaml). |
| Test di unità (bonus property-based) | 2 | `tests/unit` + **property-based** con Hypothesis in `tests/property` (9 proprietà, tra cui modello di riferimento e test differenziale tra backend). |
| Test di integrazione | 2 | `tests/integration`: contract test sui due repository, SQLite su file, rollback, vincoli dello schema, concorrenza, API HTTP, scenario end-to-end con riavvio; smoke test sul container in CI. |
| Misura di copertura dei test | 2 | `pytest-cov` con branch coverage, soglia 90 % in `pyproject.toml`, applicata in CI; report XML/HTML come artefatto. |
| Pipeline CI/CD | 3 | `.github/workflows/ci.yml`: lint → test (Python 3.10–3.12, copertura) → build + smoke test del container → publish su GHCR → release su tag. |
| Deployment in container | 2 | `Dockerfile` multi-stage non-root con health check e `docker-compose.yml` con volume persistente. |

## Convenzione dei commit

[Conventional Commits](https://www.conventionalcommits.org/): `tipo(ambito): descrizione all'imperativo`, con tipi
`feat`, `fix`, `test`, `docs`, `build`, `ci`, `chore`, `refactor`. Esempio: `feat(service): add transaction reversal`.

## Limiti noti

- "Oggi" (per il controllo delle date future) è la data UTC del server.
- Valuta unica; nessuna autenticazione.
- SQLite è adatto a un singolo host; per scalare orizzontalmente basta implementare un nuovo adattatore della porta
  `LedgerRepository` (ad es. PostgreSQL): la *contract test suite* dice quando è corretto.
