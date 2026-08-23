import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

export default defineConfig([
  ...nextVitals,
  // Dev builds go to .next-dev and production builds to .next (see
  // next.config.ts), so both must stay out of lint: the compiled chunks fail
  // no-use-before-define by construction.
  globalIgnores([".next/**", ".next-dev/**"]),
  {
    rules: {
      /**
       * A const used above where it is declared throws at runtime, and nothing
       * else here catches it: TypeScript allows it inside a function body,
       * and the build succeeds because the reference is only evaluated when
       * the code runs.
       *
       * It cost a crash on the Publish screen. A `useMemo` referenced a helper
       * declared 190 lines lower, and the predicate that touched it only ran
       * once an account was selected - so the page built, type-checked, linted
       * and rendered, and then broke on the first click.
       *
       * Functions are exempt: those are hoisted and a component that calls one
       * declared further down is ordinary React.
       */
      "no-use-before-define": ["error", { functions: false, classes: true, variables: true }],
    },
  },
]);
