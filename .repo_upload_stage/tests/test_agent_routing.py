from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import tools
from app.agent import DesktopAgent, HISTORY_LIMIT, looks_like_desktop_action, match_direct_app_command, match_direct_app_name, parse_workspace_action, parse_compound_workspace_create, looks_like_workspace_inventory, infer_workspace_folder_from_history
from app.cloud import looks_like_web_request


class AgentRoutingTests(unittest.TestCase):
    def test_everyday_chat_does_not_need_tool_definitions(self) -> None:
        self.assertFalse(looks_like_desktop_action("Explain binary search in simple terms"))
        self.assertFalse(looks_like_desktop_action("Hi Amigo, how are you?"))

    def test_desktop_and_workspace_requests_use_tools(self) -> None:
        self.assertTrue(looks_like_desktop_action("Open Notepad"))
        self.assertTrue(looks_like_desktop_action("Create a folder named ideas"))
        self.assertTrue(looks_like_desktop_action("Read the file hello.txt"))
        self.assertTrue(looks_like_desktop_action("Could you edit notes.md and replace its contents?"))
        self.assertTrue(looks_like_desktop_action("Delete old_notes.txt"))
        self.assertTrue(looks_like_desktop_action("Open the folder named ideas"))
        self.assertTrue(looks_like_desktop_action("list files"))
        self.assertTrue(looks_like_desktop_action("create folders"))
        self.assertTrue(looks_like_desktop_action("create files in my workspace"))
        self.assertTrue(looks_like_desktop_action("open folders"))


    def test_short_allowlisted_app_commands_are_resolved_without_model(self) -> None:
        self.assertEqual(match_direct_app_command("Open Notepad"), "notepad")
        self.assertEqual(match_direct_app_command("Amigo, open Chrome"), "chrome")
        self.assertEqual(match_direct_app_command("Can you please open Calculator?"), "calculator")
        # A short voice transcription may drop the verb; only allowlisted app names match.
        self.assertEqual(match_direct_app_command("yes notepad"), "notepad")
        self.assertEqual(match_direct_app_command("yeah calculator"), "calculator")
        self.assertIsNone(match_direct_app_command("Could you explain what Notepad is?"))
        self.assertIsNone(match_direct_app_command("Open the website about Notepad please"))

    @patch("app.agent.execute_tool")
    @patch("app.agent.requests.post")
    def test_short_app_command_uses_allowlisted_launch_fast_path(self, post, execute_tool) -> None:
        execute_tool.return_value = {"ok": True, "message": "Launched Notepad."}
        agent = DesktopAgent()
        agent.stage = 3
        self.assertEqual(agent.respond("yes notepad", []), "Launched Notepad.")
        execute_tool.assert_called_once_with("open_application", {"app": "notepad"})
        post.assert_not_called()

    @patch("app.agent.execute_tool")
    def test_simple_successful_folder_creation_uses_deterministic_fast_path(self, execute_tool) -> None:
        execute_tool.return_value = {"ok": True, "message": "Created folder: ideas"}
        agent = DesktopAgent()
        agent.stage = 2
        with patch.object(agent, "_ollama_chat") as chat:
            result = agent.respond("Create a folder named ideas", [])
        self.assertEqual(result, "Created folder: ideas")
        chat.assert_not_called()
        execute_tool.assert_called_once_with("create_folder", {"path": "ideas"})

    def test_tool_result_formatter_shows_file_content_without_model_paraphrase(self) -> None:
        from app.agent import format_tool_result
        text = format_tool_result("read_file", {"ok": True, "path": "notes.txt", "content": "hello"})
        self.assertIn("notes.txt", text)
        self.assertIn("hello", text)

    def test_local_context_window_is_bounded(self) -> None:
        # Keeps a short active-chat context, not the full saved history database.
        self.assertEqual(HISTORY_LIMIT, 4)

    def test_file_creation_without_filename_asks_for_filename_and_keeps_folder(self) -> None:
        parsed = parse_workspace_action("create a file under folder ideas and write what recursion means")
        self.assertEqual(parsed["action"], "create_file")
        self.assertEqual(parsed["query"], "ideas/")
        self.assertEqual(parsed["generation_topic"], "recursion")

    def test_create_file_in_named_folder_parses_topic_and_path(self) -> None:
        parsed = parse_workspace_action("create a file named notes.txt in folder ideas and write what recursion means")
        self.assertEqual(parsed["action"], "create_file")
        self.assertEqual(parsed["query"], "ideas/notes.txt")
        self.assertEqual(parsed["generation_topic"], "recursion")

    def test_compound_folder_file_and_content_request_is_one_plan(self) -> None:
        parsed = parse_compound_workspace_create("create folder study, create file notes.txt under study, and write what recursion means")
        self.assertEqual(parsed["folder_path"], "study")
        self.assertEqual(parsed["file_path"], "study/notes.txt")
        self.assertEqual(parsed["generation_topic"], "recursion")


    def test_workspace_intent_parser_does_not_confuse_editing_with_listing(self) -> None:
        parsed = parse_workspace_action("update idea-to file content to como estas")
        self.assertEqual(parsed["action"], "update")
        self.assertEqual(parsed["query"], "idea-to")
        self.assertEqual(parsed["content"], "como estas")
        self.assertEqual(parse_workspace_action("delete idea-to file")["action"], "delete")
        self.assertEqual(parse_workspace_action("open file idea-2.txt")["query"], "idea-2.txt")
        self.assertEqual(parse_workspace_action("open ideas/idea-2.txt")["query"], "ideas/idea-2.txt")
        self.assertEqual(parse_workspace_action("open file")["query"], "")
        self.assertEqual(parse_workspace_action("list files")["action"], "list")
        self.assertEqual(parse_workspace_action("show me files in my workspace")["action"], "list")
        self.assertEqual(parse_workspace_action("show file notes.txt")["action"], "read")

    def test_generic_app_names_are_resolved_without_treating_workspace_files_as_apps(self) -> None:
        self.assertEqual(match_direct_app_name("Open WhatsApp"), "WhatsApp")
        self.assertEqual(match_direct_app_name("launch Discord please"), "Discord")
        self.assertEqual(match_direct_app_name("open FileZilla"), "FileZilla")
        self.assertIsNone(match_direct_app_name("open file idea-2.txt"))
        self.assertIsNone(match_direct_app_name("open the folder named ideas"))

    def test_direct_update_finds_and_verifies_target_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch("app.tools.WORKSPACE_DIR", Path(temp)):
                (Path(temp) / "idea-to.txt").write_text("old", encoding="utf-8")
                agent = DesktopAgent()
                agent.stage = 2
                result = agent.respond("update idea-to file content to como estas", [])
                self.assertIn("Updated and verified", result)
                self.assertEqual((Path(temp) / "idea-to.txt").read_text(encoding="utf-8"), "como estas")

    def test_ambiguous_workspace_target_requires_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "idea-2.txt").write_text("file version", encoding="utf-8")
            (root / "idea 2").mkdir()
            with patch("app.tools.WORKSPACE_DIR", root), patch("app.agent.execute_tool", return_value={"ok": True, "message": "Opened item"}) as tool_call:
                agent = DesktopAgent()
                agent.stage = 2
                question = agent.respond("open Idea 2 file", [])
                self.assertIn("Which one do you mean?", question)
                self.assertIn("idea-2.txt (file)", question)
                self.assertIn("idea 2 (folder)", question)
                tool_call.assert_not_called()
                answer = agent.respond("one", [])
                self.assertEqual(answer, "Opened item")
                tool_call.assert_called_once_with("open_workspace_item", {"path": "idea-2.txt"})

    def test_generic_app_launch_uses_safe_start_menu_tool_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)), patch(
            "app.agent.execute_tool", return_value={"ok": True, "message": "Launched WhatsApp."}
        ) as tool_call, patch("app.agent.requests.post") as post:
            agent = DesktopAgent()
            agent.stage = 2
            self.assertEqual(agent.respond("Open WhatsApp", []), "Launched WhatsApp.")
            tool_call.assert_called_once_with("open_application", {"app": "WhatsApp"})
            post.assert_not_called()


    def test_direct_read_does_not_require_model_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch("app.tools.WORKSPACE_DIR", Path(temp)), patch("app.agent.requests.post") as post:
                (Path(temp) / "notes.txt").write_text("actual file content", encoding="utf-8")
                agent = DesktopAgent()
                agent.stage = 2
                result = agent.respond("read notes.txt", [])
                self.assertIn("actual file content", result)
                self.assertIn("notes.txt", result)
                post.assert_not_called()

    def test_missing_update_target_never_becomes_a_folder_listing(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)), patch("app.agent.requests.post") as post:
            agent = DesktopAgent()
            agent.stage = 2
            result = agent.respond("update missing-note file content to hello", [])
            self.assertIn("No file or folder matching", result)
            self.assertNotIn("Contents of workspace folder", result)
            post.assert_not_called()

    def test_replace_contents_of_file_phrase_resolves_target_correctly(self) -> None:
        parsed = parse_workspace_action('replace the contents of notes.txt with "new content"')
        self.assertEqual(parsed["action"], "update")
        self.assertEqual(parsed["query"], "notes.txt")
        self.assertEqual(parsed["content"], "new content")
        self.assertEqual(parsed["content_mode"], "replace")

    def test_quoted_and_unquoted_write_to_file_are_literal_updates(self) -> None:
        self.assertEqual(parse_workspace_action('write "new content" into file notes.txt')["query"], "notes.txt")
        self.assertEqual(parse_workspace_action('write "new content" into file notes.txt')["content"], "new content")
        self.assertEqual(parse_workspace_action('write hello world into file notes.txt')["content"], "hello world")

    def test_literal_write_to_existing_file_does_not_call_ollama(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)), patch("app.agent.requests.post") as post:
            path = Path(temp) / "notes.txt"
            path.write_text("old text", encoding="utf-8")
            agent = DesktopAgent()
            agent.stage = 2
            result = agent.respond('write "new content" into file notes.txt', [])
            self.assertIn("Updated and verified workspace/notes.txt", result)
            self.assertEqual(path.read_text(encoding="utf-8"), "new content")
            post.assert_not_called()

    def test_write_what_topic_means_into_file_generates_and_appends(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)):
            path = Path(temp) / "notes.txt"
            path.write_text("Existing note.", encoding="utf-8")
            agent = DesktopAgent()
            agent.stage = 2
            with patch.object(agent, "_ollama_chat", return_value={"message": {"content": "Recursion is a process that refers to itself."}}) as generation:
                result = agent.respond("write what recursion means into file notes.txt", [])
            self.assertIn("Updated and verified workspace/notes.txt", result)
            self.assertIn("Existing note.", path.read_text(encoding="utf-8"))
            self.assertIn("Recursion is a process that refers to itself.", path.read_text(encoding="utf-8"))
            generation.assert_called_once()

    def test_folder_and_file_can_be_created_in_one_natural_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)):
            agent = DesktopAgent()
            agent.stage = 2
            with patch.object(agent, "_ollama_chat", return_value={"message": {"content": "A binary search repeatedly halves a sorted search range."}}) as generation:
                result = agent.respond("Create a folder called school and a file notes.txt inside it, then write what binary search means", [])
            path = Path(temp) / "school" / "notes.txt"
            self.assertTrue(path.is_file())
            self.assertIn("Created and verified workspace/school/notes.txt", result)
            self.assertIn("binary search", path.read_text(encoding="utf-8"))
            generation.assert_called_once()

    def test_ambiguous_update_uses_file_match_and_never_lists_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)), patch("app.agent.requests.post") as post:
            root = Path(temp)
            (root / "idea 2").mkdir()
            (root / "idea-2.txt").write_text("old", encoding="utf-8")
            agent = DesktopAgent()
            agent.stage = 2
            result = agent.respond('replace the contents of "Idea 2" with "new"', [])
            self.assertIn("Updated and verified", result)
            self.assertEqual((root / "idea-2.txt").read_text(encoding="utf-8"), "new")
            self.assertNotIn("Contents of workspace folder", result)
            post.assert_not_called()

    def test_generated_paragraph_is_appended_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)):
            path = Path(temp) / "idea-to.txt"
            path.write_text("Existing text.", encoding="utf-8")
            agent = DesktopAgent()
            agent.stage = 2
            with patch.object(agent, "_ollama_chat", return_value={"message": {"content": "New paragraph about philosophy."}}) as generation:
                result = agent.respond("write a paragraph about philosophy in file idea-to.txt", [])
            self.assertIn("Updated and verified", result)
            self.assertEqual(path.read_text(encoding="utf-8"), "Existing text.\n\nNew paragraph about philosophy.\n")
            generation.assert_called_once()

    def test_delete_command_uses_confirmation_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)):
            path = Path(temp) / "delete-me.txt"
            path.write_text("important", encoding="utf-8")
            seen = []
            agent = DesktopAgent()
            agent.stage = 2
            result = agent.respond("delete delete-me file", [], confirm_delete=lambda relative: seen.append(relative) or True)
            self.assertIn("Deleted file", result)
            self.assertEqual(seen, ["delete-me.txt"])
            self.assertFalse(path.exists())

    def test_explicit_folder_listing_is_fast_and_does_not_call_model(self) -> None:
        with patch("app.agent.execute_tool", return_value={"ok": True, "path": "ideas", "items": [{"name": "note.txt", "type": "file"}]}) as tool_call, patch("app.agent.requests.post") as post:
            agent = DesktopAgent()
            agent.stage = 2
            result = agent.respond("list files in ideas folder", [])
            self.assertIn("note.txt", result)
            tool_call.assert_called_once_with("list_files", {"path": "ideas"})
            post.assert_not_called()

    def test_conversational_workspace_inventory_is_fast(self) -> None:
        self.assertTrue(looks_like_workspace_inventory("What all do I have in my workspace?"))
        with patch("app.agent.execute_tool", return_value={"ok": True, "path": ".", "items": [{"name": "ideas", "type": "folder"}]}) as tool_call, patch("app.agent.requests.post") as post:
            agent = DesktopAgent()
            agent.stage = 2
            result = agent.respond("What all do I have in my workspace?", [])
        self.assertIn("ideas (folder)", result)
        tool_call.assert_called_once_with("list_files", {"path": "."})
        post.assert_not_called()

    def test_conversational_folder_and_file_questions_route_directly(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)), patch("app.agent.requests.post") as post:
            root = Path(temp)
            (root / "ideas").mkdir()
            (root / "ideas" / "notes.txt").write_text("The actual note.", encoding="utf-8")
            agent = DesktopAgent()
            agent.stage = 2
            folder_result = agent.respond("What's inside the folder ideas?", [])
            self.assertIn("notes.txt (file)", folder_result)
            file_result = agent.respond("What does notes.txt say?", [])
            self.assertIn("The actual note.", file_result)
            post.assert_not_called()

    def test_list_in_named_folder_does_not_fall_back_to_workspace_root(self) -> None:
        with patch("app.agent.execute_tool", return_value={"ok": True, "path": "ideas", "items": [{"name": "note.txt", "type": "file"}]}) as tool_call:
            agent = DesktopAgent()
            agent.stage = 2
            result = agent.respond("list files in ideas folder", [])
        self.assertIn("note.txt", result)
        tool_call.assert_called_once_with("list_files", {"path": "ideas"})

    def test_workspace_rename_and_move_commands_use_guarded_tools_without_ollama(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)), patch("app.agent.requests.post") as post:
            root = Path(temp)
            (root / "archive").mkdir()
            (root / "notes.txt").write_text("Amigo notes", encoding="utf-8")
            agent = DesktopAgent()
            agent.stage = 2

            renamed = agent.respond("rename notes.txt to final.txt", [])
            moved = agent.respond("move final.txt into folder archive", [])

            self.assertIn("Renamed workspace file: notes.txt → final.txt", renamed)
            self.assertIn("Moved workspace file: final.txt → archive/final.txt", moved)
            self.assertEqual((root / "archive" / "final.txt").read_text(encoding="utf-8"), "Amigo notes")
            post.assert_not_called()

    def test_ambiguous_workspace_rename_asks_which_item_without_changing_any(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)), patch("app.agent.requests.post") as post:
            root = Path(temp)
            for folder in ("math", "physics"):
                (root / folder).mkdir()
                (root / folder / "notes.txt").write_text(folder, encoding="utf-8")
            agent = DesktopAgent()
            agent.stage = 2

            result = agent.respond("rename notes.txt to final.txt", [])

            self.assertIn("more than one match", result)
            self.assertIn("math/notes.txt", result)
            self.assertIn("physics/notes.txt", result)
            self.assertTrue((root / "math" / "notes.txt").exists())
            self.assertTrue((root / "physics" / "notes.txt").exists())
            post.assert_not_called()

    def test_single_create_file_with_literal_content_is_fast_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)), patch("app.agent.requests.post") as post:
            agent = DesktopAgent()
            agent.stage = 2
            result = agent.respond('create a file named note.txt containing "hello world"', [])
            self.assertIn("Created and verified workspace/note.txt", result)
            self.assertEqual((Path(temp) / "note.txt").read_text(encoding="utf-8"), "hello world")
            post.assert_not_called()

    def test_compound_folder_file_and_generated_content_is_executed_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)):
            agent = DesktopAgent()
            agent.stage = 2
            with patch.object(agent, "_ollama_chat", return_value={"message": {"content": "Recursion is when a function calls itself."}}) as model:
                result = agent.respond("Create a folder named study and create a file named notes.txt inside it and write what recursion means", [])
            saved = Path(temp) / "study" / "notes.txt"
            self.assertIn("Created and verified workspace/study/notes.txt", result)
            self.assertTrue(saved.exists())
            self.assertIn("Recursion is when a function calls itself.", saved.read_text(encoding="utf-8"))
            model.assert_called_once()

    def test_vague_reference_asks_for_name_and_topic_without_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)), patch("app.agent.requests.post") as post:
            agent = DesktopAgent()
            agent.stage = 2
            result = agent.respond("create a file under this folder and write what this thing means", [])
            self.assertIn("Which existing workspace folder", result)
            self.assertEqual(list(Path(temp).iterdir()), [])
            post.assert_not_called()

    def test_incomplete_file_creation_clarifies_folder_filename_and_topic_across_turns(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)):
            root = Path(temp)
            (root / "ideas").mkdir()
            agent = DesktopAgent()
            agent.stage = 2
            first = agent.respond("create a file under this folder and write what this thing means", [])
            self.assertIn("Which existing workspace folder", first)
            second = agent.respond("ideas", [])
            self.assertIn("What should the new file be named", second)
            third = agent.respond("definition", [])
            self.assertIn("What topic should I explain", third)
            with patch.object(agent, "_ollama_chat", return_value={"message": {"content": "Recursion is a process defined in terms of itself."}}) as generation:
                fourth = agent.respond("recursion", [])
            saved = root / "ideas" / "definition.txt"
            self.assertIn("Created and verified workspace/ideas/definition.txt", fourth)
            self.assertIn("Recursion is a process defined in terms of itself.", saved.read_text(encoding="utf-8"))
            generation.assert_called_once()

    def test_incomplete_compound_create_asks_for_new_folder_name_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)):
            agent = DesktopAgent()
            agent.stage = 2
            result = agent.respond("create a folder and file", [])
            self.assertIn("What should the new workspace folder be named", result)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_incomplete_compound_create_resumes_after_topic_clarification(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)):
            agent = DesktopAgent()
            agent.stage = 2
            first = agent.respond("create a folder named study and a file named notes.txt in it and write what this thing means", [])
            self.assertIn("What topic should I explain", first)
            with patch.object(agent, "_ollama_chat", return_value={"message": {"content": "Binary search halves a sorted search range."}}) as generation:
                result = agent.respond("binary search", [])
            saved = Path(temp) / "study" / "notes.txt"
            self.assertIn("Created and verified workspace/study/notes.txt", result)
            self.assertIn("Binary search halves", saved.read_text(encoding="utf-8"))
            generation.assert_called_once()

    def test_this_folder_uses_recent_workspace_folder_from_current_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch("app.tools.WORKSPACE_DIR", Path(temp)):
            (Path(temp) / "ideas").mkdir()
            agent = DesktopAgent()
            agent.stage = 2
            history = [{"role": "assistant", "content": "Contents of workspace folder 'ideas':\n- old.txt (file)"}]
            with patch.object(agent, "_ollama_chat", return_value={"message": {"content": "Recursion is self-reference."}}):
                result = agent.respond("create a file named definition.txt under this folder and write what recursion means", history)
            saved = Path(temp) / "ideas" / "definition.txt"
            self.assertIn("Created and verified workspace/ideas/definition.txt", result)
            self.assertTrue(saved.is_file())

    def test_qwen_thinking_is_disabled_and_model_stays_warm_by_default(self) -> None:
        fake = unittest.mock.Mock()
        fake.status_code = 200
        fake.json.return_value = {"message": {"role": "assistant", "content": "ok"}}
        fake.close.return_value = None
        with patch("app.agent.OLLAMA_MODEL", "qwen3.5:4b"), patch("app.agent.requests.post", return_value=fake) as post:
            agent = DesktopAgent()
            agent._ollama_chat([{"role": "user", "content": "hi"}], with_tools=False)
        payload = post.call_args.kwargs["json"]
        self.assertIs(payload.get("think"), False)
        self.assertEqual(payload["keep_alive"], "30m")
        self.assertEqual(payload["options"]["num_ctx"], 4096)

    def test_short_news_requests_route_to_web_research(self) -> None:
        self.assertTrue(looks_like_web_request("Find relevant AI news"))
        self.assertTrue(looks_like_web_request("Give me a tech news roundup"))


if __name__ == "__main__":
    unittest.main()
