// @ts-check
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import prettier from "eslint-config-prettier";

export default [
    // JS core rules
    js.configs.recommended,

    // TypeScript rules (non type-aware for speed/simplicity)
    ...tseslint.configs.recommended,

    // React Hooks & React Fast Refresh
    {
        files: ["**/*.{ts,tsx}"],
        plugins: {
            "react-hooks": reactHooks,
            "react-refresh": reactRefresh,
        },
        rules: {
            ...reactHooks.configs.recommended.rules,
            "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
        },
    },

    // Ignore build outputs
    {
        ignores: ["dist/", "node_modules/"],
    },

    // Disable formatting rules (Prettier owns formatting)
    prettier,
];
