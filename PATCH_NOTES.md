# Workspace workflow reliability patch

This patch fixes a deterministic parsing edge case for requests that create a file in a named folder but omit the filename. Amigo now preserves the requested folder in its clarification instead of constructing an invalid path. It also adds regression tests for a named file + folder + generated-topic request and the existing compound folder/file/content workflow.

No model, API key, workspace, database, voice model, or filesystem safety boundary is changed.

## Install
Copy `app/agent.py` and `tests/test_agent_routing.py` into the existing project, merging folders and replacing matching files.

## Test
`python -m unittest tests.test_agent_routing tests.test_tools tests.test_storage tests.test_cloud tests.test_agent_stream tests.test_voice -v`
