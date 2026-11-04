/**
 * Local type declarations for the live layer.
 *
 * `@types/node` is not a dependency of `apps/web` (deliberately: the app is a
 * browser bundle), so `process.env` needs a minimal ambient declaration.
 */
declare const process: {
  env: Record<string, string | undefined>;
};
