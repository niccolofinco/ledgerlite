# Strategia di test

```
              ┌──────────────┐
              │  smoke (CI)  │   scripts/smoke_test.sh sul container
            ┌─┴──────────────┴─┐
            │   integrazione   │   API + SQLite reale, contract tests, concorrenza
          ┌─┴──────────────────┴─┐
          │  property-based      │   invarianti su storie casuali (Hypothesis)
        ┌─┴──────────────────────┴─┐
        │        unitari           │   dominio, servizio (repo in memoria), serializer
        └──────────────────────────┘
```

| Suite | Cartella | Cosa verifica |
|---|---|---|
| **Unitari** | `tests/unit` | Money, entità di dominio, `LedgerService` con repository in memoria, serializer. Test deterministici (clock e id iniettati). |
| **Integrazione** | `tests/integration` | *Contract test* eseguiti su entrambi i repository; durabilità su file; atomicità/rollback; vincoli dello schema aggirando l'applicazione; scritture parallele da più connessioni; API HTTP con SQLite; scenario end-to-end con riavvio. |
| **Property-based** | `tests/property` | Proprietà che devono valere per *qualsiasi* storia di scritture (vedi sotto). |

## Proprietà verificate

1. `parse(format(n)) = n` per ogni importo, e `format(parse(s)) = s` per ogni stringa canonica.
2. Una transazione è valida **se e solo se** la somma dei movimenti è zero (oracolo).
3. Dopo qualsiasi sequenza di scritture il bilancio di verifica quadra e la somma dei saldi è zero (INV1, INV6).
4. I saldi (anche "alla data") coincidono con un **modello di riferimento** ingenuo (somma su liste).
5. Registrare e poi stornare una scrittura ripristina tutti i saldi; non si può stornare due volte.
6. Una scrittura sbilanciata è sempre rifiutata e non lascia tracce.
7. Ripetere una richiesta idempotente non cambia nulla.
8. Estratto conto coerente con i saldi (S1).
9. **Test differenziale**: backend in memoria e SQLite producono risultati identici sulla stessa storia.

## Copertura

La misura è configurata in `pyproject.toml` (`branch = true`, soglia `fail_under = 90`) e viene applicata dalla CI:

```bash
make cov        # pytest --cov=ledgerlite --cov-branch --cov-report=term-missing,html,xml --cov-fail-under=90
```

Il report HTML è in `htmlcov/index.html`; in CI viene caricato come artefatto e riassunto nel *job summary*.

## Qualità dei test: verifica con mutanti

Oltre alla copertura (che dice solo quali righe vengono eseguite, non se vengono *verificate*), la suite è stata
messa alla prova introducendo a mano 13 difetti deliberati ("mutanti"): storno senza inversione di segno, controllo
di bilanciamento rimosso, data futura accettata, lato normale errato, estremo di data `<` invece di `<=` (in memoria e in
SQLite), controllo di payload dell'idempotenza rimosso, conti inattivi accettati, parsing dei centesimi errato, ecc.
Undici sono stati rilevati; due sono *mutanti equivalenti* (comportamento osservabile invariato): il controllo R2 nel
servizio (duplicato dal vincolo `UNIQUE` del repository, difesa in profondità voluta) e l'ordinamento secondario `t.seq` in
una query SQLite (SQLite restituisce comunque in ordine di rowid). Un mutante sull'estremo di data in SQLite
inizialmente sopravviveva: ha rivelato una lacuna reale, colmata aggiungendo asserzioni sul giorno esatto nel contract
test e nel test differenziale.

## Eseguire i test

```bash
pip install -r requirements-dev.txt && pip install -e .
pytest                       # tutto
pytest tests/unit            # solo unitari
pytest tests/integration     # solo integrazione
pytest tests/property        # solo property-based (Hypothesis)
```
