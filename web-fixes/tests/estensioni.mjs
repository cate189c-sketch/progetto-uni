/**
 * Il progetto usa import senza estensione ("./kalman"), che Vite risolve e il
 * runtime ESM di Node no. Questo hook aggiunge .ts ai soli specificatori
 * relativi, cosi' i test girano sulle sorgenti senza toccarle.
 *
 *   node --import ./tests/estensioni.mjs --experimental-strip-types --test tests/kine.test.ts
 */
import { register } from "node:module";
import { pathToFileURL } from "node:url";

register(
  "data:text/javascript," +
    encodeURIComponent(`
      export async function resolve(spec, context, next) {
        if (spec.startsWith(".") && !/\\.[a-z]+$/.test(spec)) {
          try { return await next(spec + ".ts", context); } catch {}
        }
        return next(spec, context);
      }
    `),
  pathToFileURL("./"),
);
