# FIXES

## 1. Tracked environment file in repository
- File: api/.env
- Line: entire file
- Problem: A `.env` file containing application configuration and a Redis password was committed to the repository. This violates the requirement that `.env` files and secrets must not be committed.
- Fix: Remove the tracked `.env` file, add `.env` patterns to `.gitignore`, and move required variables into `.env.example` with placeholder values only.

## 2. API hardcoded Redis host
- File: api/main.py
- Line: Redis client initialization
- Problem: Redis host was hardcoded as `localhost`, which works poorly in containers because Redis runs in a separate service.
- Fix: Read Redis host and port from environment variables so the API can connect to the Redis service by container name.

## 3. Frontend hardcoded API URL
- File: frontend/app.js
- Line: API_URL constant
- Problem: API URL was hardcoded as `http://localhost:8000`, which breaks when frontend runs in a container and the API is in another service.
- Fix: Read API URL from environment variables.

## 4. Worker hardcoded Redis host
- File: worker/worker.py
- Line: Redis client initialization
- Problem: Worker used `localhost` for Redis, which breaks in containerized deployments.
- Fix: Read Redis host and port from environment variables.

## 5. Missing API health endpoint
- File: api/main.py
- Line: endpoints section
- Problem: API had no dedicated health endpoint for container health checks.
- Fix: Add `/health` endpoint returning a simple healthy response.

## 6. Missing frontend health endpoint
- File: frontend/app.js
- Line: routes section
- Problem: Frontend had no lightweight health endpoint for container health checks.
- Fix: Add `/health` endpoint returning a simple healthy response.

## 7. Missing Redis connection resilience
- File: api/main.py and worker/worker.py
- Line: Redis client usage
- Problem: Services assumed Redis would always be available immediately.
- Fix: Add environment-based configuration and structure the services so they work correctly with Docker health-checked startup ordering.
