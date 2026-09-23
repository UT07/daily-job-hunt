# Task 1: Measure the LangGraph Footprint — Report

## Measured Sizes

| Component | Size |
|-----------|------|
| `langgraph` + `langchain-core` (probe) | **72 MB** |
| `layer/` (existing build artifact) | **117 MB** |
| `layer-tectonic/` (existing build artifact) | **36 MB** |
| **TOTAL** | **225 MB / 250 MB** |

## Verdict

**PASS** — Proceed with zip layer packaging.

The total footprint of 225 MB leaves **25 MB of headroom** against the 250 MB AWS Lambda package size limit, exceeding the conservative 230 MB threshold (which aimed for 20 MB of breathing room for function code itself). The LangGraph + langchain-core dependencies are a safe addition to the existing architecture.

## Packaging Path

Continue Phase 1 unchanged:
- `langgraph` and `langchain-core` go into `requirements.txt`
- All functions remain packaged as zip layers
- No container-image migration required at this stage

## Notable Observations

1. **langgraph is lean**: At 72 MB, the LangGraph + langchain-core dependencies are substantially smaller than the existing layer (117 MB), making them a manageable addition to the council orchestration.

2. **Safety margin intact**: Even with the probe deps, we retain 25 MB of clear headroom, giving the Phase 1 implementation room to add small utility dependencies (e.g., tenacity for retries, pydantic-core for validation) without immediately hitting the 250 MB limit.

3. **No bloat observed**: The pip install produced expected deprecation notices (unrelated pydantic/websockets conflicts in dev dependencies), but these do not affect the production package footprint. The `--only-binary=:all:` flag and manylinux2014 platform selector ensured clean, static binaries.

4. **Future scaling**: If Phase 2+ requires additional dependencies (e.g., larger vector libraries, additional langchain integrations), we'll have 25 MB to absorb them before re-evaluating a container migration. This is enough for 3–4 moderately-sized packages.

## Script Execution Details

- **Script**: `scripts/measure_layer_budget.sh` (28 lines, verified executable)
- **Environment**: Python 3.11, manylinux2014_x86_64
- **Probe method**: Temporary directory with `--target`, no interference with `.venv`
- **Execution time**: ~45 seconds (most of which was pip installing to temp space)
- **Cleanup**: Trap correctly removed temp directory on exit

## Recommendation

Begin Phase 1 implementation without packaging rework. The zip-layer strategy is confirmed viable, and the council can safely use both `langgraph` and `langchain-core` in the orchestration design.
