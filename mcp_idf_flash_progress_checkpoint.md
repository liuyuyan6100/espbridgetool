# MCP IDF flash progress checkpoint

Date: 2026-07-05

## Context
- Workspace: `D:\code\espclaw`; bridge tool: `D:\code\espclaw\espbridgetool`.
- User symptom: MCP `flash` blocks, user cannot switch to UI to see progress, agent also cannot query progress while flash call is running.

## Key finding
- Existing progress parser/UI is already partially present (`IdfTool.flash_progress`, `/api/flash/progress`, WebSocket `flash_progress`).
- Root cause: `serial_bridge.py` `/api/flash` is `async def` but directly executes blocking `IDF.flash(...)`; this blocks the FastAPI event loop, so `/api/flash/progress` and WebSocket broadcasts cannot be served until flash finishes.
- MCP `tool_module/idf_tools.py::flash` also calls `/api/flash` with a long blocking timeout, preventing the agent from calling `get_flash_progress` during the flash.

## Patch plan
1. Add a thread-backed flash job in `serial_bridge.py`.
2. Make `/api/flash` default to non-blocking (`wait=false`), returning `job_id` immediately.
3. Keep legacy synchronous mode with `wait=true` by waiting for the worker in an executor.
4. Extend `/api/flash/progress` with job metadata/result.
5. Update MCP `flash(..., wait=False)` and tests.

## Tried approaches
- Located MCP tool source and backend endpoints via repo reads/git grep after `rg/es` were unavailable.
- Verified progress parser/UI already existed; missing piece is event-loop non-blocking orchestration.
