/**
 * @file - ESLint Flat config – Next.js core-web-vitals & TypeScript – test
 *   linting isolated to test files only – Import rules tuned for client vs.
 *   server code.
 */

// Standard Library
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Third Party Library
import js from "@eslint/js";
import ts from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import pluginImport from "eslint-plugin-import";
import pluginJSDoc from "eslint-plugin-jsdoc";
import noRelative from "eslint-plugin-no-relative-import-paths";
import pluginPerfectionist from "eslint-plugin-perfectionist";
import pluginUnicorn from "eslint-plugin-unicorn";
import globals from "globals";

// Get the current directory for FlatCompat.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Resolve ./tsconfig.json next to this config (works in Docker)
const tsconfigPath = join(__dirname, "tsconfig.json");

/**
 * Common base rules for both JS & TS files (no TS-specific checks).
 * TSDoc/TS-only rules are applied in the typed-linting block below.
 */
const baseRules = {
  /** JavaScript core recommended. */
  ...js.configs.recommended.rules,

  /** ESLint plugin import recommended. */
  ...pluginImport.configs.recommended.rules,

  /** JSDoc recommended. */
  ...pluginJSDoc.configs.recommended.rules,

  /** Unicorn recommended rules. */
  ...pluginUnicorn.configs.recommended.rules,

  /** Custom import plugin rules (enforce ordering, prevent duplicates, etc.). */
  "import/default": "error",
  "import/first": "error",
  "import/named": "error",
  "import/newline-after-import": "error",
  "import/no-absolute-path": "error",
  "import/no-cycle": "error",
  "import/no-deprecated": "warn",
  "import/no-duplicates": "error",
  "import/no-dynamic-require": "warn",
  "import/no-extraneous-dependencies": "error",
  "import/no-named-as-default": "error",
  "import/no-nodejs-modules": "off", // Allow Node.js core modules by default; override in server-side files
  "import/no-self-import": "error",
  "import/no-unresolved": "error",
  "import/order": "off", // Handled by perfectionist/sort-imports

  /**
   * Custom JSDoc rules. Will also apply to TS files. NB: It is recommended to
   * NOT use TSDoc in combination with JSDoc since TSDoc has a different syntax
   * and is sometimes not compatible with JSDoc. Pick one or the other.
   */
  "jsdoc/check-alignment": "warn", // Enforces alignment of asterisks
  "jsdoc/check-indentation": "off",
  "jsdoc/check-values": "warn",
  "jsdoc/informative-docs": "off",
  "jsdoc/require-asterisk-prefix": "warn", // Requires leading asterisks
  "jsdoc/require-description": "warn",
  "jsdoc/require-description-complete-sentence": "off",
  "jsdoc/require-file-overview": "error",
  "jsdoc/require-hyphen-before-param-description": "warn", // Enforces hyphens
  "jsdoc/require-jsdoc": [
    "error",
    {
      require: {
        ArrowFunctionExpression: true,
        ClassDeclaration: true,
        FunctionDeclaration: true,
        FunctionExpression: true,
        MethodDefinition: true,
      },
    },
  ],
  "jsdoc/require-param": "warn",
  "jsdoc/require-param-type": "off",
  "jsdoc/require-property-name": "warn",
  "jsdoc/require-property-type": "off",
  "jsdoc/require-returns": "warn",
  "jsdoc/require-returns-type": "off",
  "jsdoc/require-throws": "warn",
  "jsdoc/require-yields": "warn",
  "jsdoc/require-yields-check": "warn",
  "jsdoc/sort-tags": "off",
  "jsdoc/tag-lines": "off",

  /** General stylistic rules. */
  "max-len": "off", // Handled by prettier (i.e., run prettier first, then eslint)
  "multiline-comment-style": ["warn", "starred-block"], // Prefer /** */ comments
  "no-relative-import-paths/no-relative-import-paths": [
    // Prevent relative imports outside of their own folder.
    "error",
    {
      allowSameFolder: false,
      prefix: "@",
      rootDir: "src",
    },
  ],

  /**
   * Custom Perfectionist plugin rules for sorting imports, JSX props, named
   * imports, object properties, etc.
   */
  "perfectionist/sort-classes": [
    "error",
    {
      /*
       * default groups already put constructor before static methods,
       * but here it is explicitly in case you want to tweak later:
       */
      groups: [
        "index-signature",
        "static-property",
        "static-block",
        ["protected-property", "protected-accessor-property"],
        ["private-property", "private-accessor-property"],
        ["property", "accessor-property"],
        "constructor",
        "static-method",
        "protected-method",
        "private-method",
        "method",
        ["get-method", "set-method"],
        "unknown",
      ],
      order: "asc",
      // keep these defaults unless you want to preserve blank-line or comment partitions:
      partitionByNewLine: false,
      type: "alphabetical",
      // partitionByComment: false, // (default)
    },
  ],
  "perfectionist/sort-imports": [
    "error",
    {
      groups: [
        { commentAbove: "Standard Library" },
        ["type-builtin", "value-builtin"],
        { commentAbove: "Third Party Library" },
        ["type-external", "value-external"],
        { commentAbove: "Package Library" },
        ["type-internal", "value-internal"],
        { commentAbove: "Local Folder" },
        [
          "type-parent",
          "type-sibling",
          "type-index",
          "value-parent",
          "value-sibling",
          "value-index",
        ],
        "unknown",
      ],
      ignoreCase: true,
      newlinesBetween: 1, // One blank line between groups
      order: "asc",
      partitionByNewLine: false, // Handled by newlinesBetween
      tsconfig: { rootDir: "." },
      type: "alphabetical",
    },
  ],
  "perfectionist/sort-jsx-props": [
    "error",
    {
      customGroups: [],
      groups: [],
      ignoreCase: true,
      newlinesBetween: "ignore",
      order: "asc",
      partitionByNewLine: true,
      type: "alphabetical",
    },
  ],
  "perfectionist/sort-named-imports": [
    "error",
    {
      // Values before types inside the braces.
      groups: [["value-import", "type-import"]],
      ignoreCase: false,
      locales: "en-US-u-kf-upper", // Uppercase first within braces
      order: "asc",
      type: "alphabetical",
    },
  ],
  "perfectionist/sort-objects": [
    "error",
    {
      destructuredObjects: true,
      order: "asc",
      type: "alphabetical",
    },
  ],

  /** Disable React rules that are handled by Perfectionist plugin. */
  "react/jsx-sort-props": "off", // Handled by perfectionist/sort-jsx-props

  /** Custom Unicorn plugin rules. */
  "unicorn/better-regex": "warn",
  "unicorn/catch-error-name": "warn",
  "unicorn/empty-brace-spaces": "warn",
  "unicorn/no-array-callback-reference": "off",
  "unicorn/no-array-sort": "off",
  "unicorn/no-for-loop": "error",
  "unicorn/no-negated-condition": "off",
  "unicorn/no-null": "off",
  "unicorn/no-useless-length-check": "error",
  "unicorn/prefer-structured-clone": "off",
  "unicorn/prefer-switch": "warn",
  "unicorn/prefer-ternary": "warn",
  "unicorn/prefer-top-level-await": "warn",
  "unicorn/prevent-abbreviations": "off",
};

/** Extra rules specifically for typed-linting (TypeScript + TSDoc). */
const typedRules = {
  // TypeScript ESLint recommended.
  ...ts.configs["recommended-type-checked"].rules,
};

const eslintConfig = [
  // Ignore patterns.
  {
    ignores: [
      ".next/**",
      ".stylelintrc.js",
      "build/**",
      "coverage/**",
      "dist/**",
      "next-env.d.ts",
      "node_modules/**",
      "out/**",
      "src/components/ai-elements/**",
      "src/components/ui/**",
    ],
  },

  // Language options.
  {
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
      parser: tsParser,
    },
  },

  // Plugins.
  {
    plugins: {
      "@typescript-eslint": ts,
      "import": pluginImport,
      "jsdoc": pluginJSDoc,
      "no-relative-import-paths": noRelative,
      "perfectionist": pluginPerfectionist,
      "unicorn": pluginUnicorn,
    },
  },

  // Common settings for all files.
  {
    settings: {
      "import/resolver": {
        node: true, // Fall back to Node’s resolver for bare packages
        typescript: {
          alwaysTryTypes: true, // Follow `exports` maps to .d.ts (needed for @modelcontextprotocol/sdk subpaths)
          project: [tsconfigPath],
        },
      },
      "jsdoc": { mode: "typescript" }, // Use TSDoc for TypeScript files
      "react": {
        version: "detect", // Automatically detect the version of React
      },
    },
  },

  // Rules (after presets so we can override them).
  {
    rules: { ...baseRules },
  },

  // TS typed-linting (files: *.ts, *.tsx).
  {
    files: ["**/*.ts", "**/*.tsx"],
    languageOptions: {
      parserOptions: {
        ecmaVersion: "latest",
        project: [tsconfigPath],
        sourceType: "module",
        tsconfigRootDir: __dirname,
      },
    },
    rules: {
      ...typedRules,
      "@typescript-eslint/explicit-function-return-type": [
        "error",
        {
          allowExpressions: true,
          allowHigherOrderFunctions: true,
          allowTypedFunctionExpressions: true,
        },
      ],
      "@typescript-eslint/no-explicit-any": "off",
      "no-undef": "off", // Handled by TypeScript for TS files
      "perfectionist/sort-interfaces": [
        "error",
        { order: "asc", type: "natural" },
      ],
      "perfectionist/sort-object-types": [
        "error",
        { order: "asc", type: "natural" },
      ],
    },
  },

  // JS linting (files: *.js, *.jsx, *.cjs, *.mjs).
  {
    files: ["**/*.js", "**/*.jsx", "**/*.cjs", "**/*.mjs"],
    languageOptions: {
      /**
       * We still can use `tsParser` to parse JS (it can parse JS syntax fine),
       * but we do NOT provide `project`, so there's no typed-linting.
       */
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
      },
    },
  },

  // Server-side code may use Node built-ins like "fs", "path", etc.
  {
    files: [
      "src/api/**/*.{ts,tsx}",
      "src/app/**/*.{ts,tsx}",
      "src/pages/**/*.{ts,tsx}",
    ],
    rules: {
      // Allow Node.js core modules in these files.
      "import/no-nodejs-modules": "off",
    },
  },

  // Test linting (files: tests/**/*.ts, tests/**/*.tsx).
  {
    files: ["tests/**/*.{ts,tsx}"],
    languageOptions: {
      parserOptions: {
        ecmaVersion: "latest",
        project: [tsconfigPath],
        sourceType: "module",
        tsconfigRootDir: __dirname,
      },
    },
    rules: {
      ...typedRules,
      "no-undef": "off", // Handled by TypeScript for TS files
      "perfectionist/sort-interfaces": [
        "error",
        { order: "asc", type: "natural" },
      ],
      "perfectionist/sort-object-types": [
        "error",
        { order: "asc", type: "natural" },
      ],
    },
  },
];

export default eslintConfig;
