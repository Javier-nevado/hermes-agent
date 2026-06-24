<?php
// Opteia Kanboard — customer-portable config (bind-mounted read-only over the
// image default). Uses defined(...)...or define(...) overrides. The DB password
// is read from the environment (ABI_DB_PASSWORD, passed into the container by
// docker-compose) so this file carries NO per-customer secret and is identical
// across deployments.

// --- Postgres backend on the shared abi-db container (opteia-net) ---
// abi-db service runs as container_name "abi-memory-db" — both names resolve.
defined("DB_DRIVER")   or define("DB_DRIVER",   "postgres");
defined("DB_HOSTNAME") or define("DB_HOSTNAME", getenv("KANBOARD_DB_HOST") ?: "abi-memory-db");
defined("DB_PORT")     or define("DB_PORT",     (int) (getenv("KANBOARD_DB_PORT") ?: 5432));
defined("DB_NAME")     or define("DB_NAME",     getenv("KANBOARD_DB_NAME") ?: "kanboard");
defined("DB_USERNAME") or define("DB_USERNAME", getenv("ABI_DB_USER") ?: "abi_agent");
defined("DB_PASSWORD") or define("DB_PASSWORD", getenv("ABI_DB_PASSWORD") ?: getenv("KANBOARD_DB_PASSWORD") ?: "changeme");

// --- Runtime ---
defined("ENABLE_URL_REWRITE") or define("ENABLE_URL_REWRITE", true);
defined("LOG_DRIVER")         or define("LOG_DRIVER", "system");
defined("DEBUG")              or define("DEBUG", false);

// --- Reverse-proxy auth behind nginx + Cloudflare Access ---
defined("REVERSE_PROXY_AUTH")          or define("REVERSE_PROXY_AUTH", true);
defined("REVERSE_PROXY_USER_HEADER")   or define("REVERSE_PROXY_USER_HEADER", "HTTP_CF_ACCESS_AUTHENTICATED_USER_EMAIL");
defined("REVERSE_PROXY_EMAIL_HEADER")  or define("REVERSE_PROXY_EMAIL_HEADER", "HTTP_CF_ACCESS_AUTHENTICATED_USER_EMAIL");
defined("TRUSTED_PROXY_NETWORKS")      or define("TRUSTED_PROXY_NETWORKS", "172.16.0.0/12,127.0.0.1/32");
defined("REVERSE_PROXY_DEFAULT_ADMIN") or define("REVERSE_PROXY_DEFAULT_ADMIN", "admin");
