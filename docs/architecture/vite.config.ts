import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./": the built site works from any path, and from file:// for a quick look
export default defineConfig({ plugins: [react()], base: "./" });
