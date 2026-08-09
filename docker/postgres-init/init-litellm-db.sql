-- Runs once, only on a fresh (empty) postgres data volume — the official
-- postgres image executes everything in /docker-entrypoint-initdb.d/ on
-- first init only, after creating the primary POSTGRES_USER/POSTGRES_DB
-- (langfuse/langfuse) from this same container's own env vars.
--
-- This adds a second, separate database + user for litellm-proxy, so one
-- Postgres instance serves both Langfuse and LiteLLM's admin UI — distinct
-- logical databases and credentials, just sharing the server process.
CREATE USER litellm WITH PASSWORD 'litellm_local';
CREATE DATABASE litellm OWNER litellm;
