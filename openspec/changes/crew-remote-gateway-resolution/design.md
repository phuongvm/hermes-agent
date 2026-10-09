# Design: Crew Remote Gateway Resolution

## 1. Architectural Strategy
The solution bridges client-side Electron context with server-side reverse proxying:
1. **Client Perspective (Desktop App)**:
   - Queries Electron's connection registry through `window.hermesDesktop.getConnection()`.
   - Distinguishes between local and remote gateway profiles.
   - Points the iframe directly to the gateway's `/api/plugins/crew/board` endpoint with authentication token.
2. **Server Perspective (FastAPI Web Server)**:
   - Updates authentication middleware to recognize that iframes and standalone views on `/api/plugins/` endpoints cannot set headers, allowing URL query token bootstrap and cookie continuity.
3. **Proxy Perspective (Crew Plugin API)**:
   - Sets a scoped session cookie upon loading `/board`, allowing child requests (such as `board.json` and REST actions) to authenticate transparently via browser cookie jars.

## 2. Sequence Diagram
```
Hermes Desktop (Client)             Hermes Web Server (Gateway)         Crew Daemon (:8799)
       |                                       |                               |
       |-- getConnection() --> [remote]        |                               |
       |                                       |                               |
       |-- GET /api/plugins/crew/board?token=X |                               |
       |-------------------------------------> |                               |
       |                                       |-- GET / (upstream) ---------> |
       |                                       |<-- HTML response ------------ |
       |                                       | (rewrites URLs, sets cookie)  |
       |<-- 200 OK + Set-Cookie: hermes_session|                               |
       |                                       |                               |
       |-- GET /api/plugins/crew/board.json -- |                               |
       |   (Cookie: hermes_session=X)          |                               |
       |-------------------------------------> |-- GET /board.json ---------> |
       |                                       |<-- JSON response ------------ |
       |<-- 200 OK (live data) ----------------|                               |
```
