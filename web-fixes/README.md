# Correzioni TypeScript

File corretti da sostituire in `src/lib/kine/` e `src/components/lab/`.
Contengono solo le correzioni descritte in `../REVIEW.md` (sezioni T1–T9): il
resto del codice è invariato, così il diff resta leggibile.

```bash
npx tsc -p tsconfig.check.json      # typecheck dei moduli kine
node --import ./tests/estensioni.mjs --experimental-strip-types --test tests/kine.test.ts
```

`tests/estensioni.mjs` serve solo a far girare i test: il progetto usa import
senza estensione (`"./kalman"`), che Vite risolve e il runtime ESM di Node no.
L'hook aggiunge `.ts` ai soli specificatori relativi, senza toccare le sorgenti.

| File | Correzioni |
|---|---|
| `kalman.ts` | T9: campo `Q` morto rimosso, `qAcc` dichiarato prima dell'uso |
| `tracker.ts` | T4: `nextId` per istanza · D6/T: associazione globale invece che per traccia |
| `engine.ts` | T2: `acc -= period` · T3: tracker datato su `sentAt` · T9: guardie su webcam e loop |
| `network.ts` | T7: timer rimossi quando scattano |
| `arena.ts` | T5: distanza di mancato al posto di `meanError` |
| `types.ts` | T5: campo `missPx` su `Marker` |
| `draw.ts` | T6: canvas di appoggio riusato |
| `lab-view.tsx` | T8: barra spaziatrice · `role="switch"` sul toggle · etichette metriche |

Non applicato qui, perché cambierebbe il comportamento visibile e va deciso da
te: collegare `solveIntercept` all'assist anche nella demo web (T1). In Python
l'ho fatto — `consegna/intercept.py` più il parametro `origin` di
`compute_assist` — ed è la correzione che porta i colpi a segno dal 27% all'81%.
