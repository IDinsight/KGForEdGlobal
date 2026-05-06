/**
 * @file This is the Prettier configuration file for the project. It sets up
 *   formatting rules and integrates plugins for JSDoc and Tailwind CSS.
 */

// Standard Library
import { createRequire } from "node:module";

// Third Party Library
import * as tailwindPluginScope from "prettier-plugin-tailwindcss";

const require = createRequire(import.meta.url);

/*
 * Third Party Library
 * Require JSDoc to avoid ESM import errors
 */
const jsdocPlugin = require("prettier-plugin-jsdoc");

// Handle Tailwind's export structure (it might be .default or the scope itself)
const tailwindPlugin = tailwindPluginScope.default || tailwindPluginScope;

/**
 * Prettier Configuration.
 *
 * @type {import("prettier").Config}
 */
const config = {
  arrowParens: "always",
  bracketSameLine: false,
  bracketSpacing: true,
  htmlWhitespaceSensitivity: "css",
  jsdocLineWrappingStyle: "greedy",
  jsdocPrintWidth: 80,
  jsdocSeparateReturnsFromParam: true,
  jsdocSeparateTagGroups: true,
  jsxSingleQuote: false,
  objectWrap: "preserve",
  // Manually merge the plugins to prevent circular dependency issues.
  plugins: [
    {
      ...jsdocPlugin,
      parsers: {
        ...jsdocPlugin.parsers,
        /*
         * We inject Tailwind's typescript parser into JSDoc's config.
         * This forces JSDoc to use Tailwind as its "base", breaking the loop.
         */
        typescript: {
          ...tailwindPlugin.parsers.typescript,
          ...jsdocPlugin.parsers.typescript,
        },
      },
    },
    tailwindPlugin,
  ],
  printWidth: 80,
  proseWrap: "preserve",
  quoteProps: "consistent",
  semi: true,
  singleAttributePerLine: false,
  singleQuote: false,
  tabWidth: 2,
  trailingComma: "all",
  useTabs: false,
};

export default config;
